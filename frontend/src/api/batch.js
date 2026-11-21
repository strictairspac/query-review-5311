import{api,post}from'./client.js';
export const listBatches=()=>api('/batches');
export const getBatch=id=>api('/batches/'+id);
export const createBatch=data=>post('/batches',data);
export const createClaim=(id,data)=>post('/batches/'+id+'/claims',data);
