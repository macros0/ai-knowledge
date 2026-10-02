"use client";

import { memo, useMemo } from "react";
import { CiteLink, remarkCiteLinks } from "@/lib/chatSources";
import MarkdownViewer from "./MarkdownViewer";

const CITE_PLUGINS = [remarkCiteLinks];

export default memo(function ChatAnswer({ text, sources }) {
  const components = useMemo(() => ({ a: (props) => <CiteLink sources={sources} {...props} /> }), [sources]);
  return <MarkdownViewer className="okf-markdown chat-markdown" text={text}
    remarkPlugins={CITE_PLUGINS} components={components} />;
});
