"use client";

import { createContext, useContext, useState } from "react";

const ChatContext = createContext(null);

export function ChatProvider({ children }) {
  const [messages, setMessages] = useState([]);
  const [tags, setTags] = useState([]);
  const [pending, setPending] = useState(false);

  return (
    <ChatContext.Provider value={{ messages, tags, pending, setMessages, setTags, setPending }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
