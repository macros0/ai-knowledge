"use client";

import { useEffect, useState } from "react";
import { listTags } from "@/lib/api";

export default function TagPicker({ label, placeholder = "", selected, onChange, refreshKey = 0 }) {
  const [dictionary, setDictionary] = useState([]);
  const [input, setInput] = useState("");

  useEffect(() => {
    listTags()
      .then(setDictionary)
      .catch(() => setDictionary([]));
  }, [refreshKey]);

  const addTag = (value) => {
    const v = value.trim();
    if (!v) return;
    if (!selected.includes(v)) onChange([...selected, v]);
    setInput("");
  };

  const removeTag = (tag) => onChange(selected.filter((t) => t !== tag));

  return (
    <div className="tag-picker">
      <span className="tag-picker-label">{label}</span>
      <div className="tag-chips">
        {selected.map((t) => (
          <span key={t} className="tag-chip" onClick={() => removeTag(t)}>
            {t}
          </span>
        ))}
      </div>
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            addTag(input);
          }
        }}
        onBlur={() => addTag(input)}
        placeholder={placeholder}
        autoComplete="off"
        list="tag-datalist"
      />
      <datalist id="tag-datalist">
        {dictionary.map((t) => (
          <option key={t.name} value={t.name} />
        ))}
      </datalist>
    </div>
  );
}
