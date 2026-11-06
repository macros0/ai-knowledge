import test from "node:test";
import assert from "node:assert/strict";
import {createScenarioPreview,validateScenarioDraft,scenarioDraft} from "../src/lib/chatScenarios.mjs";

test("draft validates limits before save and preserves literal instructions",()=>{
 assert.equal(validateScenarioDraft(scenarioDraft()),false);
 assert.equal(validateScenarioDraft({name:"x".repeat(120),description:"d".repeat(1000),instructions:"i".repeat(12000),visibility:"private"}),true);
 assert.equal(validateScenarioDraft({name:"x",description:"",instructions:" ",visibility:"private"}),false);
});
test("obsolete preview cannot replace the new preview; cancellation aborts only its own signal",async()=>{
 const pending=[],events=[];
 const preview=createScenarioPreview({send:(body,options)=>new Promise(resolve=>pending.push({resolve,options,body})),onChange:e=>events.push(e)});
 const first=preview.run({query:"old"});const second=preview.run({query:"new"});
 assert.equal(pending[0].options.signal.aborted,true);
 pending[0].options.onText("old");pending[0].resolve({answer:"old"});await first;
 pending[1].options.onText("new");pending[1].resolve({answer:"new"});await second;
 assert.equal(events.filter(e=>e.type==="result").at(-1).data.answer,"new");
 assert.equal(events.some(e=>e.type==="delta"&&e.text==="old"),false);
 preview.cancel();assert.equal(pending[1].options.signal.aborted,true);
});


import {componentHarness} from "./support/componentHarness.mjs";
import * as scenarios from "../src/lib/chatScenarios.mjs";
const Modal=()=>null,Editor=()=>null;
const source={id:"a",name:"Saved",description:"",instructions:"old",visibility:"private",version:1,can_edit:true};

test("late catalog edit response cannot replace a new unsaved draft",async()=>{
 let resolveEdit;
 const api={listAnswerScenarios:async()=>({items:[source],total:1}),getAnswerScenario:()=>new Promise(resolve=>{resolveEdit=resolve;}),friendlyApiError:()=>"error"};
 const h=componentHarness(new URL("../src/components/AnswerScenarioManager.jsx",import.meta.url),{
   "@/i18n/LocaleContext":{useI18n:()=>({t:key=>key})},"@/lib/api":api,"./Modal":Modal,"./AnswerScenarioEditor":Editor,
 },{onSelect:()=>{},onChanged:()=>{},onClose:()=>{},search:{}});
 h.render();await new Promise(resolve=>setTimeout(resolve,180));h.render();
 const opening=h.find(node=>node.type==="button"&&node.props.children==="scenario.edit").props.onClick();
 h.find(node=>node.type==="button"&&node.props.children==="scenario.create").props.onClick();
 assert.equal(h.render().props.initial,null);
 resolveEdit(source);await opening;
 assert.equal(h.render().props.initial,null);
 h.dispose();
});

test("copying a saved scenario cannot discard unsaved edits without confirmation",async()=>{
 let copied=0,confirmed=0;
 const h=componentHarness(new URL("../src/components/AnswerScenarioEditor.jsx",import.meta.url),{
   "@/i18n/LocaleContext":{useI18n:()=>({t:key=>key})},"@/lib/api":{copyAnswerScenario:async()=>{copied++;return {...source,id:"copy"};},friendlyApiError:()=>"error"},
   "@/lib/chatScenarios.mjs":scenarios,"./Modal":Modal,"./ChatAnswer":()=>null,
 },{initial:source,search:{},onCopied:()=>{},onSaved:()=>{},onClose:()=>{}},{window:{confirm:()=>{confirmed++;return false;}}});
 h.render();h.find(node=>node.type==="input"&&node.props.value==="Saved").props.onChange({target:{value:"Unsaved"}});h.render();
 await h.find(node=>node.type==="button"&&node.props.children==="scenario.copy").props.onClick();
 assert.equal(confirmed,1);assert.equal(copied,0);
 assert.equal(h.render().props.children.props.children[0].props.children[1].props.value,"Unsaved");
 h.dispose();
});


test("admin edit permission does not move another user's public scenario into Mine",async()=>{
 const items=[{...source,id:"mine",owner_id:"admin",can_edit:true},{...source,id:"public",owner_id:"other",visibility:"public",can_edit:true}];
 const h=componentHarness(new URL("../src/components/ChatScenarioPicker.jsx",import.meta.url),{
  "@/i18n/LocaleContext":{useI18n:()=>({t:key=>key})},"@/lib/api":{listAnswerScenarios:async()=>({items}),friendlyApiError:()=>"error"},"./AnswerScenarioManager":()=>null,
 },{ownerId:"admin",onIdChange:()=>{},onSelectedChange:()=>{},search:{}});
 h.render();await new Promise(resolve=>setImmediate(resolve));h.render();
 const mine=h.find(node=>node.type==="optgroup"&&node.props.label==="scenario.mine");
 const publicGroup=h.find(node=>node.type==="optgroup"&&node.props.label==="scenario.public");
 assert.deepEqual(mine.props.children.map(node=>node.props.value),["mine"]);
 assert.deepEqual(publicGroup.props.children.map(node=>node.props.value),["public"]);
 h.dispose();
});
