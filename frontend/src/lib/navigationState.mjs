export function activeNavigationHref(pathname,items) {
  return items.filter(item => item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`))
    .sort((a,b)=>b.href.length-a.href.length)[0]?.href ?? null;
}
export function documentView(params,canUpload=true) {
  if(!canUpload && (params.get("view") === "upload" || params.has("upload_dev") || params.has("upload_module"))) return "docs";
  if(params.has("upload_dev") || params.has("upload_module")) return "upload";
  return ["upload","trash"].includes(params.get("view")) ? params.get("view") : "docs";
}
export function documentViewUrl(params,view) {
  const next=new URLSearchParams(params);
  next.delete("view"); next.delete("upload_dev"); next.delete("upload_module");
  if(view !== "docs") next.set("view",view);
  return next.size ? `/?${next}` : "/";
}
