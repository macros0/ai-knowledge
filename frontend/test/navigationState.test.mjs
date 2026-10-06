import test from "node:test";
import assert from "node:assert/strict";
import { activeNavigationHref, documentView, documentViewUrl } from "../src/lib/navigationState.mjs";
const items=[{href:"/",exact:true},{href:"/chat"},{href:"/chat/history"},{href:"/chat/history/admin"}];
test("navigation selects only most specific segment-boundary match",()=>{
 assert.equal(activeNavigationHref("/chat/history/admin",items),"/chat/history/admin");
 assert.equal(activeNavigationHref("/chat/history",items),"/chat/history");
 assert.equal(activeNavigationHref("/chatty",items),null);
 assert.equal(activeNavigationHref("/",items),"/");
});
test("upload prefill opens upload; changing view preserves list filters",()=>{
 assert.equal(documentView(new URLSearchParams("upload_dev=7")),"upload");
 assert.equal(documentView(new URLSearchParams("view=wrong")),"docs");
 assert.equal(documentViewUrl(new URLSearchParams("q=SAP&status=done&upload_dev=7"),"trash"),"/?q=SAP&status=done&view=trash");
 assert.equal(documentViewUrl(new URLSearchParams("view=trash&q=SAP"),"docs"),"/?q=SAP");
});

test("viewer upload URL falls back to the document list", () => {
  assert.equal(documentView(new URLSearchParams("view=upload"),false),"docs");
  assert.equal(documentView(new URLSearchParams("upload_module=HR"),false),"docs");
  assert.equal(documentView(new URLSearchParams("view=trash"),false),"trash");
});
