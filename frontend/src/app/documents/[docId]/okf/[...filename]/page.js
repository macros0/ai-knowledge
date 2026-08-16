import { notFound } from "next/navigation";
import Link from "next/link";

export const dynamic = "force-dynamic";

function decodeSegment(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export async function generateMetadata({ params }) {
  const { filename } = await params;
  const decodedPath = filename.map(decodeSegment).join("/");
  return { title: `${decodedPath} — OKF Knowledge Service` };
}

export default async function OkfFilePage({ params }) {
  const { docId, filename } = await params;
  const decodedPath = filename.map(decodeSegment);
  const filePath = decodedPath.map(encodeURIComponent).join("/");

  const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
  const resp = await fetch(`${backendUrl}/api/documents/${docId}/okf/${filePath}`, {
    cache: "no-store",
  });

  if (!resp.ok) notFound();

  const text = await resp.text();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href="/">
        ← Назад к документам
      </Link>
      <h1>{decodedPath.join("/")}</h1>
      <pre>{text}</pre>
    </div>
  );
}
