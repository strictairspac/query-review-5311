"""无单号对账：按客户、线路与金额容差做一对一配对。

规则固定为：同客户、同线路，金额相差不超过 0.01；同值的重复结算行各占一份，
但在判断“配法”时不区分彼此（它们互换不构成另一种配法）。

纯计算模块，不访问数据库，便于独立验证：

- 每一维（同一客户、线路组）都是“按金额排序后的同序配对”：若存在任一份全配，
  同序配对也一定可行（一维容差匹配的非交叉性质），因此不按“最近金额”逐行贪心；
- 配对结论按金额类别的流量判断：全配不存在 → different；全配存在但金额类流量不唯一
  → pending_claim；唯一 → passed；
- 认领是一组被固定的行对。固定后只在剩余行上重新判断能否配齐、是否唯一；
  多条认领合并后若无解，则拒绝后到的认领，请其重选。
"""
from bisect import bisect_left, bisect_right
from decimal import Decimal, InvalidOperation

TOLERANCE = Decimal('0.01')
REQUIRED_COLUMNS = ('customer', 'route', 'amount')
DEFAULT_CANDIDATE_LIMIT = 200


class ShapeError(ValueError):
    """结果列或金额无法用于无单号对账。"""


class ClaimError(ValueError):
    """认领本身不合法，或与已有认领合并后无法配齐。"""


# ---------------------------------------------------------------- 解析与行视图

def locate_columns(columns):
    """按列名定位 customer / route / amount，各要求恰好一列。

    精确比对按列位置且接受任意别名；无单号对账必须知道客户、线路和金额是哪一列，
    因此这里要求三列都以标准名出现（别名会让人误判配对口径，直接拒绝更安全）。
    """
    names = [str(c).strip().lower() for c in columns]
    index = {}
    for field in REQUIRED_COLUMNS:
        hits = [i for i, name in enumerate(names) if name == field]
        if len(hits) != 1:
            shown = '、'.join(str(c) for c in columns) or '（无列）'
            raise ShapeError(
                '无单号对账要求两侧结果各恰好包含一列 customer、route、amount；'
                f'当前列为 {shown}'
            )
        index[field] = hits[0]
    return index


def parse_side(columns, rows, side='原始'):
    """把执行结果转成带原始行号的配对记录。"""
    index = locate_columns(columns)
    records = []
    for row_no, row in enumerate(rows):
        raw = row[index['amount']]
        if raw is None:
            raise ShapeError(f'{side}结果第 {row_no + 1} 行金额为空，无法对账')
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise ShapeError(f'{side}结果第 {row_no + 1} 行金额不是数字：{raw}')
        if not amount.is_finite():
            raise ShapeError(f'{side}结果第 {row_no + 1} 行金额不是有限数字：{raw}')
        records.append({
            'index': row_no,
            'key': (row[index['customer']], row[index['route']]),
            'customer': row[index['customer']],
            'route': row[index['route']],
            'amount': amount,
        })
    return records


def public_rows(records):
    """给页面/JSON 的稳定行视图，index 即认领时使用的行号。"""
    return [{
        'index': r['index'],
        'customer': r['customer'],
        'route': r['route'],
        'amount': str(r['amount']),
    } for r in sorted(records, key=lambda r: r['index'])]


def records_from_public(rows):
    """从证据 JSON 中的行视图还原计算记录。"""
    return [{
        'index': int(r['index']),
        'key': (r.get('customer'), r.get('route')),
        'customer': r.get('customer'),
        'route': r.get('route'),
        'amount': Decimal(str(r['amount'])),
    } for r in rows]


def allowed(left, right):
    return left['key'] == right['key'] and abs(left['amount'] - right['amount']) <= TOLERANCE


def _describe_group(key):
    return f'客户「{key[0]}」线路「{key[1]}」'


# ---------------------------------------------------------------- 单组配平

def _sorted_pairing(left_rows, right_rows):
    """同组内按金额排序后同序配对；不可配齐返回 None。"""
    if len(left_rows) != len(right_rows):
        return None
    left_sorted = sorted(left_rows, key=lambda r: (r['amount'], r['index']))
    right_sorted = sorted(right_rows, key=lambda r: (r['amount'], r['index']))
    pairs = []
    for a, b in zip(left_sorted, right_sorted):
        if abs(a['amount'] - b['amount']) > TOLERANCE:
            return None
        pairs.append((a['index'], b['index']))
    return left_sorted, right_sorted, pairs


def _flow(left_sorted, right_sorted):
    """把同序配对聚合成“金额类别 -> 份数”的流量。"""
    counts = {}
    for a, b in zip(left_sorted, right_sorted):
        counts[(a['amount'], b['amount'])] = counts.get((a['amount'], b['amount']), 0) + 1
    return counts


def _residual_graph(left_sorted, right_sorted, flow):
    """残量网络：L->R 表示金额容许的一份，R->L 表示该类别对已有正流量可减一份。"""
    left_amounts = sorted({r['amount'] for r in left_sorted})
    right_amounts = sorted({r['amount'] for r in right_sorted})
    out = {('L', a): [] for a in left_amounts}
    out.update({('R', b): [] for b in right_amounts})
    for a in left_amounts:
        lo = bisect_left(right_amounts, a - TOLERANCE)
        hi = bisect_right(right_amounts, a + TOLERANCE)
        out[('L', a)] = [('R', b) for b in right_amounts[lo:hi]]
    for (a, b), count in flow.items():
        if count > 0:
            out.setdefault(('R', b), []).append(('L', a))
    return out


def _strong_components(out):
    """Kosaraju 强连通分量，迭代实现避免递归深度。"""
    nodes = list(out)
    order = []
    seen = set()
    for root in nodes:
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(out[root]))]
        while stack:
            node, iterator = stack[-1]
            advanced = False
            for nxt in iterator:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, iter(out.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                order.append(node)
                stack.pop()
    reverse = {node: [] for node in nodes}
    for node, targets in out.items():
        for target in targets:
            reverse.setdefault(target, []).append(node)
    comps = []
    assigned = set()
    for root in reversed(order):
        if root in assigned:
            continue
        comp = []
        stack = [root]
        assigned.add(root)
        while stack:
            node = stack.pop()
            comp.append(node)
            for target in reverse.get(node, ()):
                if target not in assigned:
                    assigned.add(target)
                    stack.append(target)
        comps.append(comp)
    return comps


def _path_within(comp, out, start, goal, banned_edge):
    """在分量内 BFS 找 start→goal 的路径，可禁用一条边。"""
    allowed_nodes = set(comp)
    previous = {start: None}
    queue = [start]
    while queue:
        node = queue.pop(0)
        for target in out.get(node, ()):
            if target not in allowed_nodes:
                continue
            if (node, target) == banned_edge:
                continue
            if target in previous:
                continue
            previous[target] = node
            if target == goal:
                path = [goal]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return list(reversed(path))
            queue.append(target)
    return None


def _find_flow_cycle(left_sorted, right_sorted, flow):
    """找一条非平凡的“类别对”交替环，存在即表示还有别的配法。

    残量网络是二分图；长度 2 的环（L->R->L）只是同一类别对的沿原路往返，
    不构成新的配法。长度 4 及以上的环才是真正的另一种配法。
    强连通分量大小 >= 3 即包含这样的环（二分图无奇环）。
    """
    out = _residual_graph(left_sorted, right_sorted, flow)
    for comp in _strong_components(out):
        if len(comp) < 3:
            continue
        members = set(comp)
        for node in comp:
            for target in out.get(node, ()):
                if target not in members:
                    continue
                path = _path_within(comp, out, target, node, (target, node))
                if path and len(path) >= 2:
                    cycle = [node, target] + path[1:]
                    return cycle
    return None


def _shift_flow(flow, cycle):
    """沿环移动一份流量，得到另一份全配的流量。"""
    shifted = dict(flow)
    for first, second in zip(cycle, cycle[1:]):
        if first[0] == 'L' and second[0] == 'R':
            key = (first[1], second[1])
            shifted[key] = shifted.get(key, 0) + 1
        else:
            key = (second[1], first[1])
            shifted[key] = shifted.get(key, 0) - 1
    return {k: v for k, v in shifted.items() if v > 0}


def _materialize(left_rows, right_rows, flow):
    """把金额类别流量落实为具体行对，行按 index 顺序依次占用。"""
    by_amount_left = {}
    by_amount_right = {}
    for r in sorted(left_rows, key=lambda r: r['index']):
        by_amount_left.setdefault(r['amount'], []).append(r)
    for r in sorted(right_rows, key=lambda r: r['index']):
        by_amount_right.setdefault(r['amount'], []).append(r)
    cursor_left = {a: 0 for a in by_amount_left}
    cursor_right = {a: 0 for a in by_amount_right}
    pairs = []
    for (a, b), count in sorted(flow.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        for _ in range(count):
            left_row = by_amount_left[a][cursor_left[a]]
            right_row = by_amount_right[b][cursor_right[b]]
            cursor_left[a] += 1
            cursor_right[b] += 1
            pairs.append((left_row['index'], right_row['index']))
    return pairs


def _classes(rows):
    classes = {}
    for r in rows:
        classes.setdefault(r['amount'], []).append(r)
    for items in classes.values():
        items.sort(key=lambda r: r['index'])
    return classes


def _remainder_feasible(left_rows, right_rows, left_index, right_index):
    """固定一条行对后，剩余行是否仍能配齐。"""
    return _sorted_pairing(
        [r for r in left_rows if r['index'] != left_index],
        [r for r in right_rows if r['index'] != right_index],
    ) is not None


# ---------------------------------------------------------------- 认领校验

def _validate_claims(left, right, claims):
    """检查每条认领的边界、重复占用和客户/线路/金额条件。

    行号是结果行视图里的 index，不假定它等于列表下标。
    """
    left_by_index = {r['index']: r for r in left}
    right_by_index = {r['index']: r for r in right}
    seen_left = {}
    seen_right = {}
    unique = []
    for pair in claims:
        try:
            li, rj = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            raise ClaimError('认领行号格式不正确')
        if li not in left_by_index or rj not in right_by_index:
            raise ClaimError(f'认领行号超出结果范围：左侧 #{li} / 右侧 #{rj}')
        if li in seen_left and seen_left[li] != rj:
            raise ClaimError(f'左侧第 #{li} 行已被认领到另一行，不能重复认领')
        if rj in seen_right and seen_right[rj] != li:
            raise ClaimError(f'右侧第 #{rj} 行已被认领到另一行，不能重复认领')
        if (li, rj) in unique:
            continue
        first, second = left_by_index[li], right_by_index[rj]
        if first['key'] != second['key']:
            raise ClaimError(f'认领的左侧 #{li} 与右侧 #{rj} 客户或线路不同')
        if abs(first['amount'] - second['amount']) > TOLERANCE:
            raise ClaimError(f'认领的左侧 #{li} 与右侧 #{rj} 金额相差超过 0.01')
        seen_left[li] = rj
        seen_right[rj] = li
        unique.append((li, rj))
    return unique


# ---------------------------------------------------------------- 结论计算

def _group_report(left_rows, right_rows):
    """计算单个客户/线路组的配平结论。"""
    pairing = _sorted_pairing(left_rows, right_rows)
    if pairing is None:
        return {
            'feasible': False,
            'issue': (
                f'左侧剩余 {len(left_rows)} 行、右侧剩余 {len(right_rows)} 行，'
                '或存在金额差超过 0.01 而无法配对的行'
            ),
            'pairs': None,
            'alternate_pairs': None,
            'multiple': False,
        }
    left_sorted, right_sorted, pairs = pairing
    flow = _flow(left_sorted, right_sorted)
    cycle = _find_flow_cycle(left_sorted, right_sorted, flow)
    alternate_pairs = None
    if cycle:
        alternate_pairs = _materialize(left_sorted, right_sorted, _shift_flow(flow, cycle))
    return {
        'feasible': True,
        'issue': None,
        'pairs': pairs,
        'alternate_pairs': alternate_pairs,
        'multiple': cycle is not None,
    }


def _claim_candidates(left_rows, right_rows, key, limit):
    """列出“认领后剩余仍可配齐”的金额类别对。

    同一金额类别只给一对代表行：同值重复行互换不算另一种配法。固定一对后再检查
    剩余是否仍能配齐，因此列出的每一对都能被认领而不把余下判死。
    """
    left_classes = _classes(left_rows)
    right_classes = _classes(right_rows)
    right_amounts = sorted(right_classes)
    out = []
    for a in sorted(left_classes):
        lo = bisect_left(right_amounts, a - TOLERANCE)
        hi = bisect_right(right_amounts, a + TOLERANCE)
        for b in right_amounts[lo:hi]:
            if len(out) >= limit:
                return out, True
            rep_left = left_classes[a][0]['index']
            rep_right = right_classes[b][0]['index']
            if _remainder_feasible(left_rows, right_rows, rep_left, rep_right):
                out.append({
                    'customer': key[0],
                    'route': key[1],
                    'left_amount': str(a),
                    'right_amount': str(b),
                    'left_rows': [r['index'] for r in left_classes[a]],
                    'right_rows': [r['index'] for r in right_classes[b]],
                })
    return out, False


def evaluate(left, right, claims=(), candidate_limit=DEFAULT_CANDIDATE_LIMIT):
    """计算当前认领下的配对结论。

    left/right 为 parse_side 的记录；claims 为 (左侧原始行号, 右侧原始行号)。
    """
    claims = _validate_claims(left, right, claims)
    claimed_left = {li for li, _ in claims}
    claimed_right = {rj for _, rj in claims}

    def gather(records, excluded):
        groups = {}
        for r in records:
            if r['index'] not in excluded:
                groups.setdefault(r['key'], []).append(r)
        return groups

    left_groups = gather(left, claimed_left)
    right_groups = gather(right, claimed_right)
    keys = sorted(set(left_groups) | set(right_groups), key=lambda k: (str(k[0]), str(k[1])))

    matching = list(claims)
    alternate = list(claims)
    multiples = []
    issue = None
    for key in keys:
        report = _group_report(left_groups.get(key, []), right_groups.get(key, []))
        if not report['feasible']:
            issue = f'{_describe_group(key)} 无法配齐：{report["issue"]}'
            break
        matching.extend(report['pairs'])
        alternate.extend(report['alternate_pairs'] if report['multiple'] else report['pairs'])
        if report['multiple']:
            multiples.append(key)

    if issue:
        return {
            'status': 'different',
            'matching_count': 0,
            'matching': None,
            'example_matchings': [],
            'candidate_classes': [],
            'candidates_truncated': False,
            'pair_count': None,
            'claims': [list(c) for c in claims],
            'issue': issue,
        }

    candidate_classes = []
    truncated = False
    if multiples:
        for key in multiples:
            if len(candidate_classes) >= candidate_limit:
                truncated = True
                break
            found, cut = _claim_candidates(
                left_groups.get(key, []), right_groups.get(key, []), key,
                candidate_limit - len(candidate_classes),
            )
            candidate_classes.extend(found)
            truncated = truncated or cut

    multiple = bool(multiples)
    return {
        'status': 'pending_claim' if multiple else 'passed',
        'matching_count': 2 if multiple else 1,
        'matching': sorted(matching),
        'example_matchings': [sorted(matching), sorted(alternate)] if multiple else [],
        'candidate_classes': candidate_classes,
        'candidates_truncated': truncated,
        'pair_count': len(matching),
        'claims': [list(c) for c in claims],
        'issue': None,
    }


def evaluate_public(result, claims=()):
    """从证据 JSON 还原并计算，供详情页和认领接口共用。"""
    left = records_from_public(result['left_rows'])
    right = records_from_public(result['right_rows'])
    return evaluate(left, right, claims)


def build_evidence(original, candidate):
    """在原有严格比对结果旁附加无单号对账的初始结论。"""
    left = parse_side(original['columns'], original['rows'], '原始')
    right = parse_side(candidate['columns'], candidate['rows'], '候选')
    initial = evaluate(left, right)
    return {
        'mode': 'ticketless',
        'tolerance': str(TOLERANCE),
        'rule': '同客户、同线路、金额相差不超过 0.01；同值重复行各占一份',
        'left_columns': original['columns'],
        'right_columns': candidate['columns'],
        'left_rows': public_rows(left),
        'right_rows': public_rows(right),
        'initial_status': initial['status'],
        'left_count': len(left),
        'right_count': len(right),
    }
