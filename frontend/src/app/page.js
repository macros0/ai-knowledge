import { Suspense } from "react";
import DocumentsPanel from "@/components/DocumentsPanel";

export default function DocumentsPage() {
  return (
    <Suspense fallback={null}>
      <DocumentsPanel />
    </Suspense>
  );
}
