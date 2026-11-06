"use client";
import {useState} from "react";
import {useI18n} from "@/i18n/LocaleContext";
import Modal from "./Modal";
export default function ChatScenarioBadge({scenario,execution}) {
  const {t}=useI18n();const [open,setOpen]=useState(false);
  if(!scenario)return null;
  return <div className="scenario-badge">
    <button className="btn ghost" type="button" onClick={()=>setOpen(true)}>{t("scenario.used",{name:scenario.name,version:scenario.version})}</button>
    {execution?.status==="protected_authorship" && <div className="meta">{t("scenario.protectedAuthorship")}</div>}
    {open && <Modal title={t("scenario.used",{name:scenario.name,version:scenario.version})} onClose={()=>setOpen(false)}
      footer={<button className="modal-btn" type="button" onClick={()=>setOpen(false)}>{t("scenario.close")}</button>}>
      <p>{scenario.description}</p><pre className="scenario-instructions">{scenario.instructions}</pre>
    </Modal>}
  </div>;
}
