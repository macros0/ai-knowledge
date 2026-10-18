"use client";

import {dictionaryComparison, applyDictionaryComparison} from "@/lib/referenceDictionary.mjs";
import ReferenceConflictDialog from "./ReferenceConflictDialog";

export default function ReferenceDictionaryConflictDialog({base, local, remote, onConfirm, onClose}) {
  const comparison = dictionaryComparison(base, local, remote);
  return <ReferenceConflictDialog {...comparison} labels={Object.fromEntries(comparison.fields.map(key => [key, key]))}
    onClose={onClose} onConfirm={values => onConfirm(applyDictionaryComparison(remote, values))} />;
}
