import EditorLeaseNotice from "./EditorLeaseNotice";

export default function ReferenceEditNotice({ session, onCopy }) {
  if (session.status?.lease_required === false) return null;
  return <EditorLeaseNotice state={session.state} ownerName={session.status?.owner_name}
    remainingSeconds={session.countdown} onContinue={() => session.markActivity()}
    onRetry={session.acquire} onCopy={onCopy} />;
}
