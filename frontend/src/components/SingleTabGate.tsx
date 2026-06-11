/**
 * SingleTabGate — allow only ONE live AgentGUI tab/window per browser.
 *
 * Two open copies of the app are actively harmful, not just wasteful: each
 * desk's live activity stream is a single server-side queue, so two activity
 * WebSockets steal alternating tokens from each other (fragmented chat), and
 * two FloorManagers patrol/audit/nudge the same desks concurrently.
 *
 * Mechanism: the Web Locks API. The first tab acquires an exclusive lock and
 * holds it for its lifetime (auto-released by the browser on close/crash).
 * Any later tab fails the `ifAvailable` probe, renders a blocking overlay
 * instead of the app, and queues a waiting lock request — so it activates
 * automatically the moment the holder goes away. "Use here instead" broadcasts
 * a takeover: the holder releases its lock (unmounting the app and closing all
 * its WebSockets) and shows a "moved to another tab" screen.
 *
 * Browsers without `navigator.locks` (non-secure contexts, very old engines)
 * skip gating entirely — same behavior as before this component existed.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

const LOCK_NAME = "agent-gui-single-tab";
const TAKEOVER_CHANNEL = "agent-gui-tab-takeover";

type GateStatus = "pending" | "active" | "blocked" | "deactivated";

const hasLocks = typeof navigator !== "undefined" && "locks" in navigator;

export default function SingleTabGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<GateStatus>(hasLocks ? "pending" : "active");
  // Resolving this promise releases the held lock (lets another tab take over).
  const releaseRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!hasLocks) return;
    const abort = new AbortController();
    const bc = new BroadcastChannel(TAKEOVER_CHANNEL);
    let dead = false;

    // Queue for the lock. Granted immediately if free; otherwise this waits
    // until the current holder closes or releases, then activates this tab.
    navigator.locks
      .request(LOCK_NAME, { signal: abort.signal }, async () => {
        if (dead) return;
        setStatus("active");
        await new Promise<void>((resolve) => { releaseRef.current = resolve; });
      })
      .catch(() => { /* request aborted on unmount */ });

    // If someone else already holds the lock, our request above is parked in
    // the queue — flip to the blocked overlay (never downgrade an active tab).
    navigator.locks.query().then((state) => {
      if (dead) return;
      if (state.held?.some((l) => l.name === LOCK_NAME)) {
        setStatus((s) => (s === "pending" ? "blocked" : s));
      }
    }).catch(() => {});

    bc.onmessage = (e) => {
      // Another tab requested takeover. Only the holder reacts: release the
      // lock (the waiter is granted next) and go passive. BroadcastChannel
      // never echoes to the sender, so the taker can't deactivate itself.
      if (e.data === "takeover" && releaseRef.current) {
        releaseRef.current();
        releaseRef.current = null;
        setStatus("deactivated");
      }
    };

    return () => {
      dead = true;
      abort.abort();
      releaseRef.current?.();   // StrictMode remount / real unmount: free the lock
      releaseRef.current = null;
      bc.close();
    };
  }, []);

  if (status === "active") return <>{children}</>;
  if (status === "pending") return null; // lock grant resolves in microseconds

  const blocked = status === "blocked";
  return (
    <div style={{
      height: "100vh", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 14,
      background: "var(--bg)", color: "var(--text)", textAlign: "center", padding: 24,
    }}>
      <div style={{ fontSize: 42 }}>{blocked ? "🔒" : "👋"}</div>
      <div style={{ fontSize: 18, fontWeight: 600 }}>
        {blocked
          ? "AgentGUI is already open in another tab or window"
          : "This session moved to another tab"}
      </div>
      <div style={{ color: "var(--text-dim)", maxWidth: 440, fontSize: 13, lineHeight: 1.5 }}>
        {blocked
          ? "Running two copies splits the live agent streams between them and " +
            "doubles manager patrols, so only one tab can be active at a time. " +
            "Close the other tab to continue here, or take over now."
          : "Another tab took over this AgentGUI session. Reload to claim it back."}
      </div>
      {blocked ? (
        <button
          onClick={() => {
            // The waiting lock request from the effect is already queued; the
            // holder releases on this message and we activate automatically.
            const c = new BroadcastChannel(TAKEOVER_CHANNEL);
            c.postMessage("takeover");
            c.close();
          }}
          style={btnStyle}
        >
          Use here instead
        </button>
      ) : (
        <button onClick={() => location.reload()} style={btnStyle}>
          Reload &amp; claim this tab
        </button>
      )}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: "var(--accent2)", color: "#fff", border: "none",
  borderRadius: "var(--radius)", padding: "10px 18px", fontSize: 14,
  fontWeight: 600, cursor: "pointer",
};
