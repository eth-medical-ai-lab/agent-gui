import { useEffect, useRef, useState } from "react";
import type { AgentProfile } from "./types";
import { api } from "./api/client";
import { dispatchRosterPlace } from "./rosterLayout";

type AgentDrag = {
  kind: "stop" | "assign";
  sessionId: string;
  agentId: string;
  color?: string;
  state: "idle" | "working" | "thinking";
  x: number;
  y: number;
};

function deskAtPoint(x: number, y: number): { id: string; pending: boolean } | null {
  const stack = document.elementsFromPoint(x, y);
  for (const el of stack) {
    if (!(el instanceof HTMLElement)) continue;
    if (el.hasAttribute("data-roster-backdrop")) continue;
    const id = el.getAttribute("data-desk-id");
    if (id) return { id, pending: el.getAttribute("data-desk-pending") === "1" };
  }
  return null;
}

/** The roster section under the cursor, if any (for drag-to-section). */
function sectionAtPoint(x: number, y: number): string | null {
  const stack = document.elementsFromPoint(x, y);
  for (const el of stack) {
    if (!(el instanceof HTMLElement)) continue;
    const id = el.getAttribute("data-roster-section");
    if (id) return id;
  }
  return null;
}

export function useAgentDrag({
  rosterRef,
  agents,
  onSessionInterrupt,
  onAssignAgentToDesk,
  onRosterAgentClick,
  onRosterOpen,
}: {
  rosterRef: React.RefObject<HTMLDivElement | null>;
  agents?: AgentProfile[];
  onSessionInterrupt?: (id: string) => void;
  onAssignAgentToDesk?: (deskId: string, agentId: string) => void;
  onRosterAgentClick?: (agentId: string) => void;
  onRosterOpen?: () => void;
}) {
  const [agentDrag, setAgentDrag] = useState<AgentDrag | null>(null);
  const [rosterHover, setRosterHover] = useState(false);
  const [deskDropHoverId, setDeskDropHoverId] = useState<string | null>(null);
  const [sectionDropHoverId, setSectionDropHoverId] = useState<string | null>(null);
  const dragStartRef = useRef<(Omit<AgentDrag, "x" | "y"> & { startX: number; startY: number }) | null>(null);

  function rosterContains(x: number, y: number): boolean {
    const el = rosterRef.current;
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  function handleAgentDragStart(
    e: React.MouseEvent, sessionId: string, agentId: string,
    color?: string, state: "idle" | "working" | "thinking" = "working",
  ) {
    dragStartRef.current = { kind: "stop", sessionId, agentId, color, state, startX: e.clientX, startY: e.clientY };
  }

  function handleRosterAgentDragStart(e: React.MouseEvent, agentId: string, color?: string) {
    e.preventDefault();
    dragStartRef.current = { kind: "assign", sessionId: "", agentId, color, state: "idle", startX: e.clientX, startY: e.clientY };
    onRosterOpen?.();
  }

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const start = dragStartRef.current;
      if (!start) return;
      if (Math.hypot(e.clientX - start.startX, e.clientY - start.startY) < 8) return;
      setAgentDrag({
        kind: start.kind, sessionId: start.sessionId, agentId: start.agentId,
        color: start.color, state: start.state, x: e.clientX, y: e.clientY,
      });
      onRosterOpen?.();
      if (start.kind === "stop") {
        setRosterHover(rosterContains(e.clientX, e.clientY));
        setDeskDropHoverId(null);
        setSectionDropHoverId(null);
      } else if (start.kind === "assign") {
        setRosterHover(false);
        if (rosterContains(e.clientX, e.clientY)) {
          // Inside the roster → dropping onto a section re-buckets the profile.
          setDeskDropHoverId(null);
          setSectionDropHoverId(sectionAtPoint(e.clientX, e.clientY));
        } else {
          const desk = deskAtPoint(e.clientX, e.clientY);
          setDeskDropHoverId(desk ? desk.id : null);
          setSectionDropHoverId(null);
        }
      }
    }
    function onUp(e: MouseEvent) {
      const start = dragStartRef.current;
      dragStartRef.current = null;
      if (start) {
        const dragged = Math.hypot(e.clientX - start.startX, e.clientY - start.startY) >= 8;
        if (dragged && start.kind === "stop" && rosterContains(e.clientX, e.clientY)) {
          api.sessions.interrupt(start.sessionId).catch(() => {});
          onSessionInterrupt?.(start.sessionId);
        } else if (dragged && start.kind === "assign" && rosterContains(e.clientX, e.clientY)) {
          // Dropped back inside the roster → move the profile into the section
          // under the cursor (if any).
          const sectionId = sectionAtPoint(e.clientX, e.clientY);
          if (sectionId) dispatchRosterPlace(start.agentId, sectionId);
        } else if (dragged && start.kind === "assign" && !rosterContains(e.clientX, e.clientY)) {
          const desk = deskAtPoint(e.clientX, e.clientY);
          if (desk) onAssignAgentToDesk?.(desk.id, start.agentId);
        } else if (!dragged && start.kind === "assign") {
          onRosterAgentClick?.(start.agentId);
        }
      }
      setAgentDrag(null);
      setRosterHover(false);
      setDeskDropHoverId(null);
      setSectionDropHoverId(null);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onAssignAgentToDesk, onRosterAgentClick, onRosterOpen, onSessionInterrupt, rosterRef]);

  return {
    agentDrag,
    rosterHover,
    deskDropHoverId,
    sectionDropHoverId,
    handleAgentDragStart,
    handleRosterAgentDragStart,
  };
}

export type AgentDragState = AgentDrag | null;
