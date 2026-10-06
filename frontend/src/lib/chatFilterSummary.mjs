export function buildChatFilterSummary({tags=[],moduleFilter="",development=null,sourceLocale="",mailMode="all",searchScopeEnabled=false,searchScopeDocuments=[]}={}) {
  const result=[searchScopeEnabled ? {id:"scope",key:"chat.scopeTitle",params:{count:searchScopeDocuments.length},removable:true} : {id:"scope",key:"ux.scopeAll"}];
  for(const tag of tags) result.push({id:`tag:${tag}`,key:"ux.filterTag",params:{name:tag},removable:true});
  if(moduleFilter) result.push({id:"module",key:"ux.filterModule",params:{name:moduleFilter},removable:true});
  if(development) result.push({id:"development",key:"ux.filterDevelopment",params:{name:development.number || development.id},removable:true});
  result.push(sourceLocale ? {id:"locale",key:sourceLocale === "unknown" ? "docs.localeUnknown" : "ux.filterLocale",params:{name:sourceLocale},removable:true} : {id:"locale",key:"docs.allLocales"});
  result.push({id:"mail",key:mailMode === "exclude" ? "chat.mailModeExclude" : mailMode === "only" ? "chat.mailModeOnly" : "chat.mailModeAll",removable:mailMode !== "all"});
  return result;
}
