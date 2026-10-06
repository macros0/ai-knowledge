export function retryableUploadItems(items,files) {
  return items.filter(item=>["failed","uncertain","not-sent"].includes(item.state) && files.has(item.id));
}
export function updateUploadItem(items,id,state,details={}) {
  return items.map(item=>item.id === id ? {id:item.id,filename:item.filename,state,...details} : item);
}
