export function shouldSubmitQuestion({key,shiftKey=false,isComposing=false}) {
  return key === "Enter" && !shiftKey && !isComposing;
}
export function retryRequestOptions(message) {
  return Object.fromEntries(["requestUseGlossary", "requestMailMode", "requestTags", "requestTopK", "requestMode", "requestSearchDepth", "requestSourceSelection", "requestDocIds", "requestSourceLocale", "responseMode"].map(key => [key,message[key]]));
}

export function freshSourceSearchOptions(message) {
  const options=retryRequestOptions(message);
  delete options.requestSourceSelection;
  return {...options,responseMode:"documents"};
}

export function chatNetworkScopeOptions(options) {
  return {sourceSelection:options.requestSourceSelection, searchDocIds:options.requestSourceSelection ? undefined : options.requestDocIds};
}
