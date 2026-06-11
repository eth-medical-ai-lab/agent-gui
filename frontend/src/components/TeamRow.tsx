/**
 * TeamRow — a single team's desk strip with its own agent, bed, and team manager.
 * Extracted from Office.tsx to support multiple independent team rows.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AgentProfile, DeskItem, FilePreviewData, PendingAssignment, ReasoningEffort, Session, Team, ToolPresetId, ToolsetMeta } from "../types";
import { teamDisplayName } from "../types";
import { api } from "../api/client";
import { playBell } from "../sounds";
import { AgentFigure, type AgentArchetype } from "./AgentFigure";
import { SceneBackground, SCENES, DEFAULT_SCENE } from "./SceneBackground";
import { sceneFloorChrome, type SceneFloorChrome } from "../sceneFloorChrome";
import { PendingTaskDesk } from "./PendingTaskDesk";
import { useAvatarPrefs } from "../avatarPrefs";
import { deskIsRunning, resolveDeskProfileVisual, type DeskConfigView } from "../deskConfig";
import { TaskDesk } from "./TaskDesk";
import { DeskSettingsPanel } from "./DeskSettingsPanel";
import { isVllmBackend } from "../backendKind";
import { TeamRowPanelContext, TEAM_ROW_HEIGHT } from "../TeamRowPanelContext";
import { FloorManager } from "./FloorManager";
import { ManagerModelMenu } from "./ManagerModelMenu";
import { TeamFileRepo } from "./TeamFileRepo";

// ── Team color palette ──────────────────────────────────────────────────────

const TEAM_COLOR_BG: Record<string, string> = {
  blue:   "rgba(50,80,220,0.07)",
  red:    "rgba(220,50,50,0.07)",
  green:  "rgba(40,180,80,0.07)",
  purple: "rgba(160,50,220,0.07)",
  orange: "rgba(220,120,30,0.07)",
};
const TEAM_COLOR_ACCENT: Record<string, string> = {
  blue:   "#5080e0",
  red:    "#e05050",
  green:  "#40b860",
  purple: "#a050e0",
  orange: "#e0882a",
};

// ── Sub-components ──────────────────────────────────────────────────────────

function AddDeskButton({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 200,
        height: 324,
        border: `1px dashed rgba(255,255,255,${hover ? 0.3 : 0.12})`,
        borderRadius: 8,
        display: "flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer", flexShrink: 0,
        transition: "border-color 0.2s, opacity 0.2s",
        opacity: hover ? 0.8 : 0.4,
      }}
    >
      <span style={{ fontSize: 40, color: "var(--text-dim)", lineHeight: 1, userSelect: "none" }}>+</span>
    </div>
  );
}

interface WalkState {
  fromX: number; toX: number;
  fromY: number; toY: number;
  dir: 1 | -1;
  visual?: AvatarVisual;
}

type SleepPhase = "awake" | "walking-to-bed" | "sleeping" | "waking";

function isLiveSession(d: DeskItem): boolean {
  if ("isPending" in d) return true;
  const s = d as Session;
  return !s.ended_at && s.is_running !== false;
}

function isSessionSolved(d: DeskItem): boolean {
  return !("isPending" in d) && !!(d as Session).task_solved;
}

/** Desk the roaming avatar should visit (skip finished/solved-only desks). */
function deskNeedsRoamingAgent(d: DeskItem): boolean {
  if ("isPending" in d) return true;
  const s = d as Session;
  if (s.task_solved) return false;
  return isLiveSession(d);
}

/** A stopped desk with prior work that can be resumed — e.g. after a bed-stop
 *  interrupted (and slept) every agent, so the session is no longer "live" but
 *  still has unfinished history. Ringing the bell should resume these, not just
 *  re-seat the avatar. */
function deskResumable(d: DeskItem): boolean {
  if ("isPending" in d) return false;
  const s = d as Session;
  return !s.ended_at && !s.task_solved && s.message_count > 0;
}

function findPreferredDeskIndex(desks: DeskItem[], preferIdx: number): number {
  const prefer = desks[preferIdx];
  if (prefer && (deskNeedsRoamingAgent(prefer) || deskResumable(prefer))) return preferIdx;
  // After a bed-stop nothing is "live", so fall back to a resumable desk (its
  // prior task) before an empty pending desk.
  const resumable = desks.findIndex(deskResumable);
  if (resumable >= 0) return resumable;
  const pending = desks.findIndex((d) => "isPending" in d);
  if (pending >= 0) return pending;
  const active = desks.findIndex((d) => deskNeedsRoamingAgent(d));
  if (active >= 0) return active;
  return Math.max(0, Math.min(preferIdx, desks.length - 1));
}

function teamHasActiveWork(desks: DeskItem[]): boolean {
  return desks.some((d) => {
    if ("isPending" in d) return false;
    const s = d as Session;
    return isLiveSession(d) && !s.task_solved;
  });
}

function deskShowsAvatar(
  isPending: boolean,
  isPrimary: boolean,
  executing: boolean,
): boolean {
  // Every running desk keeps its avatar; focus moves a second figure to the clicked desk.
  if (executing) return true;
  if (isPrimary) return true;
  if (isPending) return false;
  return false;
}

type AvatarVisual = {
  agentId: string;
  color: string;
  archetype?: AgentArchetype;
  isPrototype?: boolean;
  cloneFrom?: string | null;
};

function agentStateForDesk(desk: DeskItem | undefined): "idle" | "working" | "thinking" {
  if (!desk) return "idle";
  if ("isPending" in desk) return "thinking";
  if (!isLiveSession(desk)) return "idle";
  const s = desk as Session;
  return s.message_count > 0 ? "working" : "thinking";
}

function SpeechBubble({ text }: { text: string }) {
  return (
    <div style={{
      position: "absolute", bottom: "calc(100% + 6px)", left: "50%",
      transform: "translateX(-50%)", background: "white", color: "#1a1a2e",
      borderRadius: 10, padding: "5px 10px", fontSize: 11, fontWeight: 500,
      whiteSpace: "nowrap", boxShadow: "0 3px 12px rgba(0,0,0,0.35)",
      zIndex: 15, animation: "fnbob 2.6s ease-in-out infinite", transformOrigin: "bottom center",
    }}>
      {text}
      <div style={{
        position: "absolute", bottom: -6, left: "50%", transform: "translateX(-50%)",
        borderLeft: "6px solid transparent", borderRight: "6px solid transparent", borderTop: "6px solid white",
      }} />
    </div>
  );
}

/** Settings gear button — matches the top-bar SettingsMenu wheel (⚙ glyph). */
function PixelSettingsButton({ active, onClick }: { active?: boolean; onClick: (e: React.MouseEvent) => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title="Agent settings"
      style={{
        flexShrink: 0, padding: 0, zIndex: 6,
        width: 22, height: 22, borderRadius: 6, cursor: "pointer",
        display: "flex", alignItems: "center", justifyContent: "center",
        background: active ? "var(--accent2)" : "rgba(255,255,255,0.06)",
        border: "1px solid var(--card-border)",
        color: active ? "white" : "var(--text)",
        fontSize: 14, lineHeight: 1,
      }}
    >
      ⚙
    </button>
  );
}

function WalkingAgent({ fromX, toX, fromY, toY, dir, visual }: WalkState) {
  const [x, setX] = useState(fromX);
  const [y, setY] = useState(fromY);
  useEffect(() => {
    const raf = requestAnimationFrame(() => { setX(toX); setY(toY); });
    return () => cancelAnimationFrame(raf);
  }, [toX, toY]);
  return (
    <div style={{
      position: "fixed", left: x, top: y,
      transform: dir === -1 ? "scaleX(-1)" : "none",
      transition: "left 0.48s ease-in-out, top 0.48s ease-in-out",
      zIndex: 200, pointerEvents: "none",
    }}>
      <AgentFigure
        walking
        scale={1}
        agentId={visual?.agentId || undefined}
        color={visual?.color}
        archetype={visual?.archetype}
        isPrototype={visual?.isPrototype}
        cloneFrom={visual?.cloneFrom}
      />
    </div>
  );
}

/** Left chrome column width — keeps desk columns aligned across teams. */
const TEAM_CHROME_WIDTH = 236;
/** Manager + file repo tiles share one footprint in the team chrome column. */
const TEAM_CHROME_TILE_WIDTH = 108;
const TEAM_CHROME_TILE_MIN_HEIGHT = 78;

function BellButton({ onClick, glowing }: { onClick: () => void; glowing?: boolean }) {
  const [hov, setHov] = useState(false);
  const lit = hov || glowing;
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      title={glowing ? "Agents waking…" : "Ring bell — stop all agents (or wake when sleeping)"}
      style={{
        cursor: "pointer", userSelect: "none", flexShrink: 0,
        filter: lit ? "drop-shadow(0 0 6px #ffd060)" : "none",
        transition: "filter 0.2s",
      }}
    >
      <svg width="28" height="32" viewBox="0 0 28 32">
        <rect x="3" y="26" width="22" height="5" rx="2.5" fill={lit ? "#a07828" : "#7a5820"} />
        <path d="M14 3 C7 3 4 10 4 24 L24 24 C24 10 21 3 14 3 Z" fill={lit ? "#f0c040" : "#c09030"} />
        <path d="M11 6 C8 9 7 14 7 19 C9 13 11 9 13 7 Z" fill="rgba(255,245,150,0.45)" />
        <rect x="4" y="23" width="20" height="2" fill={lit ? "#c0a020" : "#9a7820"} />
        <circle cx="14" cy="4" r="2.5" fill={lit ? "#c09020" : "#906810"} />
        <circle cx="14" cy="3" r="1.5" fill={lit ? "#f0c040" : "#c09030"} />
      </svg>
      {glowing && (
        <>
          {[1, 1.6].map((scale, i) => (
            <div key={i} style={{
              position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
              borderRadius: "50%", border: "1px solid rgba(255,208,60,0.5)",
              animation: `bell-ring 0.9s ${i * 0.3}s ease-out infinite`,
              pointerEvents: "none", transform: `scale(${scale})`,
            }} />
          ))}
        </>
      )}
    </div>
  );
}

function Bed({ isSleeping, selected, bedRef, onClick, chrome }: {
  isSleeping: boolean; selected?: boolean;
  bedRef: React.RefObject<HTMLDivElement>; onClick?: () => void;
  chrome: SceneFloorChrome;
}) {
  const [hov, setHov] = useState(false);
  const lit = (isSleeping && (hov || selected));
  return (
    <div
      ref={bedRef} onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      title={isSleeping ? (selected ? "Click a desk to assign agent there" : "Click to select desk for agent") : "Stop all agents on this team"}
      style={{
        cursor: "pointer", userSelect: "none", flexShrink: 0,
        filter: lit ? "drop-shadow(0 0 8px rgba(100,160,255,0.8))" : "none",
        transition: "filter 0.2s",
        position: "relative",
      }}
    >
      <svg width="86" height="48" viewBox="0 0 54 30" style={{ display: "block" }}>
        <rect x="0" y="2" width="4" height="26" rx="1" fill="#5a3e22" />
        <rect x="1" y="3" width="1" height="24" fill="#8a6040" opacity="0.5" />
        <rect x="50" y="13" width="4" height="15" rx="1" fill="#5a3e22" />
        <rect x="4" y="22" width="46" height="7" fill="#6b4c2a" />
        <rect x="4" y="22" width="46" height="2" fill="#7a5530" />
        <rect x="4" y="13" width="46" height="10" fill="#7878a0" />
        <rect x="5" y="14" width="44" height="1" fill="#9898c0" opacity="0.7" />
        <rect x="5" y="14" width="11" height="8" rx="1" fill="#d0d0f0" />
        <rect x="6" y="15" width="9" height="1" fill="#eeeeff" opacity="0.8" />
        {isSleeping ? (
          <>
            <rect x="4" y="9" width="10" height="5" rx="1" fill="#2a3512" />
            <rect x="4" y="13" width="2" height="6" fill="#2a3512" />
            <rect x="12" y="13" width="2" height="5" fill="#3a4a1a" />
            <rect x="4" y="12" width="11" height="10" rx="2" fill="#ffe0b0" />
            <rect x="6" y="15" width="3" height="1" fill="#1a1a0a" />
            <rect x="10" y="15" width="3" height="1" fill="#1a1a0a" />
            <rect x="7" y="17" width="4" height="1" rx="0.5" fill="#cc8866" />
            <rect x="15" y="13" width="34" height="10" fill="#4a3aaa" />
            <rect x="15" y="13" width="34" height="2" fill="#6a5acc" />
            <ellipse cx="34" cy="18" rx="11" ry="3.5" fill="#5a4abb" />
          </>
        ) : null}
      </svg>
      {isSleeping && (
        <div style={{ position: "absolute", top: -8, left: 88, display: "flex", gap: 3, alignItems: "flex-end", pointerEvents: "none" }}>
          {["z","z","Z"].map((c, i) => (
            <span key={i} style={{
              fontSize: 9 + i * 4, fontWeight: 700,
              color: `rgba(${160 + i * 20},${160 + i * 20},255,0.9)`,
              animation: `slpZ 2.4s ease-in-out ${i * 0.55}s infinite`,
              display: "inline-block", textShadow: "0 0 6px rgba(120,120,255,0.5)",
            }}>{c}</span>
          ))}
        </div>
      )}
      <div style={{
        position: "absolute", top: 50, left: 0, width: 86, textAlign: "center",
        fontSize: 8, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", lineHeight: 1,
        color: hov
          ? (isSleeping ? chrome.labelAccent : chrome.labelWarn)
          : chrome.labelDim,
        transition: "color 0.2s", pointerEvents: "none", userSelect: "none",
      }}>
        {isSleeping ? "Wake all non-solved tasks" : "Stop all agents"}
      </div>
    </div>
  );
}

function ManagerStagingArea({ stagingRef, onPatrol, onClick, chrome, agents }: {
  stagingRef: React.RefObject<HTMLDivElement>;
  onPatrol: boolean;
  onClick: () => void;
  chrome: SceneFloorChrome;
  agents: AgentProfile[];
}) {
  const [hov, setHov] = useState(false);
  return (
    <div
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        position: "relative",
        width: TEAM_CHROME_TILE_WIDTH,
        minHeight: TEAM_CHROME_TILE_MIN_HEIGHT,
        flexShrink: 0,
        userSelect: "none",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 3,
        padding: "8px 6px",
        borderRadius: 8,
        cursor: onPatrol ? "default" : "pointer",
        background: hov && !onPatrol ? "rgba(100,160,255,0.10)" : chrome.controlBg,
        border: `1px solid ${hov && !onPatrol ? chrome.labelAccent : chrome.controlBorder}`,
        transition: "background 0.15s, border-color 0.15s",
      }}
    >
      <ManagerModelMenu chrome={chrome} agents={agents} />
      <div
        ref={stagingRef}
        onClick={onClick}
        title={onPatrol ? "Manager is on patrol" : "Click to send manager on a patrol round"}
        style={{
          width: "100%",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          gap: 3,
        }}
      >
        <svg width="40" height="24" viewBox="0 0 44 26" style={{ flexShrink: 0 }}>
          <rect x="0" y="4" width="44" height="6" rx="1" fill={hov && !onPatrol ? "#3a3060" : "#2a2050"} />
          <rect x="1" y="4" width="42" height="2" fill="rgba(255,255,255,0.12)" />
          <rect x="3" y="10" width="3" height="14" rx="1" fill="#1a1540" />
          <rect x="38" y="10" width="3" height="14" rx="1" fill="#1a1540" />
          <rect x="16" y="0" width="12" height="8" rx="1" fill={onPatrol ? "#2a3040" : "#1e3050"} />
          <rect x="17" y="1" width="10" height="5" rx="0.5" fill={onPatrol ? "#1a2030" : "#0a1828"} />
          <rect x="21" y="8" width="2" height="3" fill="#1a1540" />
          <rect x="4" y="3" width="10" height="6" rx="0.5" fill="#d4c890" />
          <rect x="5" y="4" width="8" height="1" fill="#b8a860" />
          <rect x="5" y="6" width="6" height="1" fill="#b8a860" />
        </svg>
        <div style={{
          fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
          color: onPatrol ? chrome.labelAccent : (hov ? chrome.labelHover : chrome.labelDim),
          textTransform: "uppercase", transition: "color 0.2s", lineHeight: 1.2, textAlign: "center",
        }}>
          {onPatrol ? "patrolling" : "manager"}
        </div>
      </div>
    </div>
  );
}

// ── Team name (click to rename) ─────────────────────────────────────────────

function TeamNameLabel({
  label, onRename, chrome,
}: {
  label: string;
  onRename?: (name: string) => void;
  chrome: SceneFloorChrome;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      setDraft(label);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing, label]);

  function commit() {
    onRename?.(draft);
    setEditing(false);
  }

  if (!onRename) {
    return (
      <span style={{
        fontSize: 13, fontWeight: 700, color: "inherit", opacity: 0.95,
      }}>
        {label}
      </span>
    );
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { e.preventDefault(); setEditing(false); }
        }}
        onClick={(e) => e.stopPropagation()}
        maxLength={48}
        style={{
          width: 140, height: 22, borderRadius: 4,
          border: `1px solid ${chrome.inputBorder}`,
          background: chrome.inputBg, color: chrome.inputColor,
          fontSize: 12, fontWeight: 600, padding: "0 6px",
          textTransform: "none", letterSpacing: 0,
        }}
      />
    );
  }

  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); setEditing(true); }}
      title="Click to rename team"
      style={{
        background: "transparent", border: "none", padding: 0, margin: 0,
        fontSize: 13, fontWeight: 700, color: "inherit", opacity: 0.95,
        cursor: "text", maxWidth: 160, overflow: "hidden",
        textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
  );
}

// ── Main TeamRow component ──────────────────────────────────────────────────

export interface TeamRowProps {
  team: Team;
  teamIndex: number;
  onDeleteTeam?: () => void;
  justStartedId?: string | null;
  justStartedAnchor?: { top: number; left: number } | null;
  onJustStartedConsumed?: () => void;
  workspacePaths?: Record<string, string>;
  taskContents?: Record<string, string>;
  taskImages?: Record<string, { name: string; url: string }[]>;
  pendingTexts?: Record<string, string>;
  verbose?: boolean;
  reasoningEffort?: import("../types").ReasoningEffort;
  apiMode?: import("../types").ApiMode;
  bellSound?: string;
  scene?: string;
  onSceneChange?: (sceneId: string) => void;
  onTeamRename?: (name: string) => void;
  showManager?: boolean;
  managerPatrolIntervalSec?: number;
  managerIdleGraceSec?: number;
  askManagerDeskId?: string | null;
  agents?: AgentProfile[];
  pendingAssignments?: Record<string, PendingAssignment>;
  activePendingDeskId?: string | null;
  /** Pending desk id currently under an agent drag cursor. */
  deskDropHoverId?: string | null;
  onAskManagerDone?: () => void;
  onPreview: (data: FilePreviewData) => void;
  deskPanelZ?: Record<string, number>;
  onDeskPanelActivate?: (deskId: string) => void;
  onDeskStart: (deskId: string, msg: string, agentId: string, images?: { name: string; url: string }[], anchor?: { top: number; left: number }) => Promise<void>;
  onDeskClose: (deskId: string) => void;
  onAddDesk: () => void;
  onSessionInterrupt?: (id: string) => void;
  onPendingMsgChange?: (id: string, msg: string) => void;
  onPendingAssignmentPatch?: (deskId: string, patch: Partial<PendingAssignment>) => void;
  onActivePendingDeskChange?: (deskId: string | null) => void;
  onDeskFocus?: (deskId: string) => void;
  focusedDeskId?: string | null;
  selectedDeskId?: string | null;
  deskConfigsById?: Record<string, DeskConfigView>;
  onAvatarClick?: (deskId: string) => void;
  onDeskAskManager?: (sessionId: string) => void;
  /** Begin dragging a desk's agent avatar (drop on the bench to stop it). */
  onAgentDragStart?: (e: React.MouseEvent, sessionId: string, agentId: string, color?: string, state?: "idle" | "working" | "thinking") => void;
  searchMatchIds?: Set<string>;
  // Per-desk agent-settings panel (gear next to the avatar).
  toolsets?: ToolsetMeta[];
  reasoningValue?: ReasoningEffort;
  reasoningOptions?: { value: ReasoningEffort; label: string }[];
  onDeskConfigProfileChange?: (deskId: string, agentId: string) => void;
  onDeskConfigModelChange?: (deskId: string, model: string) => void;
  onDeskConfigToolsChange?: (deskId: string, preset: ToolPresetId, enabled: string[]) => void;
  onDeskConfigReasoningChange?: (v: ReasoningEffort) => void;
}

export function TeamRow({
  team, teamIndex, onDeleteTeam,
  justStartedId, justStartedAnchor, onJustStartedConsumed, workspacePaths, taskContents, taskImages, pendingTexts,
  verbose, reasoningEffort, apiMode, bellSound, scene, onSceneChange, onTeamRename, showManager,
  managerPatrolIntervalSec, managerIdleGraceSec,
  askManagerDeskId, agents, pendingAssignments, activePendingDeskId, deskDropHoverId,
  onActivePendingDeskChange, onDeskFocus, focusedDeskId, selectedDeskId, deskConfigsById,
  onAvatarClick,
  onAskManagerDone, onPreview, onDeskStart, onDeskClose,
  deskPanelZ, onDeskPanelActivate,
  onAddDesk, onSessionInterrupt, onPendingMsgChange, onPendingAssignmentPatch, onDeskAskManager, onAgentDragStart,
  searchMatchIds,
  toolsets, reasoningValue, reasoningOptions,
  onDeskConfigProfileChange, onDeskConfigModelChange, onDeskConfigToolsChange, onDeskConfigReasoningChange,
}: TeamRowProps) {
  const avatars = useAvatarPrefs();
  const desks = team.desks;
  const teamLabel = teamDisplayName(team, teamIndex);

  const scrollRef  = useRef<HTMLDivElement>(null);
  const deskRefs   = useRef<Map<number, HTMLDivElement>>(new Map());
  const bedRef     = useRef<HTMLDivElement>(null);
  const bellRef    = useRef<HTMLDivElement>(null);
  const stagingRef = useRef<HTMLDivElement>(null);
  const [panelRoot, setPanelRoot] = useState<HTMLElement | null>(null);

  const [managerOnPatrol, setManagerOnPatrol] = useState(false);
  const [scrollPct,       setScrollPct]       = useState(0);

  // Hide desk strip until real sessions load (prevents reload scroll-jump)
  const [desksVisible, setDesksVisible] = useState(() => {
    try {
      const v2 = localStorage.getItem("hermes-workbench-v2");
      if (v2) {
        const data = JSON.parse(v2);
        const saved = data?.teams?.find((t: { id: string }) => t.id === team.id);
        // Only stay hidden if there's a real session to wait for — otherwise
        // an all-pending (empty) team would never reveal its desk strip.
        return !saved?.items?.some((it: { type?: string }) => it.type === "session");
      }
      // V1 backward compat (team 0 only)
      if (teamIndex === 0) {
        const v1 = localStorage.getItem("hermes-workbench-v1");
        return !v1 || JSON.parse(v1).length === 0;
      }
      return true;
    } catch { return true; }
  });

  const [agentDeskIndex, setAgentDeskIndex] = useState<number>(() => Math.max(0, desks.length - 1));
  const [agentSelected, setAgentSelected]   = useState(false);
  // Transient line the agent says on the desk it just walked to (e.g. "already done").
  const [agentSay,      setAgentSay]        = useState<string | null>(null);
  const [bedSelected,   setBedSelected]     = useState(false);
  const [bellRinging,   setBellRinging]     = useState(false);
  // Desk whose ⚙ agent-settings panel is open (keyed by desk id), + its anchor.
  const [settingsDesk,  setSettingsDesk]    = useState<{ deskId: string; anchor: HTMLElement } | null>(null);
  const [walk,          setWalk]            = useState<WalkState | null>(null);
  const [isWalking,     setIsWalking]       = useState(false);
  const [sleepPhase,    setSleepPhase]      = useState<SleepPhase>("awake");

  useEffect(() => {
    if (sleepPhase !== "awake") setManagerOnPatrol(false);
  }, [sleepPhase]);

  // The ⚙ settings panel belongs to the focused desk — close it when focus moves.
  useEffect(() => {
    setSettingsDesk((cur) => (cur && cur.deskId !== selectedDeskId ? null : cur));
  }, [selectedDeskId]);

  // Stable mutable refs
  const agentDeskIndexRef  = useRef(agentDeskIndex);
  const sleepPhaseRef      = useRef<SleepPhase>("awake");
  const isWalkingRef       = useRef(false);
  const managerOnPatrolRef = useRef(false);
  managerOnPatrolRef.current = managerOnPatrol;
  const lastServerActivityRef = useRef(Date.now());
  const desksRef = useRef(desks);
  desksRef.current = desks;
  const lastActiveDeskRef = useRef(agentDeskIndex);
  const instantScrollRef  = useRef(true);
  const lastAvatarVisualRef = useRef<AvatarVisual>({ agentId: "", color: "#6a7a9a" });

  // Re-anchor agent to rightmost desk on length change
  useLayoutEffect(() => {
    const idx = Math.max(0, desks.length - 1);
    instantScrollRef.current = true;
    agentDeskIndexRef.current = idx;
    setAgentDeskIndex(idx);
  }, [desks.length]);

  // Reveal desks once real sessions are present
  useEffect(() => {
    if (!desksVisible && desks.some(d => !("isPending" in d))) {
      setDesksVisible(true);
    }
  }, [desks, desksVisible]);

  // Scroll the desk strip horizontally so the focused desk is visible — without
  // scrollIntoView, which also nudges the vertical floor scroll to fit panels.
  useLayoutEffect(() => {
    const el = deskRefs.current.get(agentDeskIndex);
    const strip = scrollRef.current;
    if (!el || !strip) return;
    const pad = 24;
    const elLeft = el.offsetLeft;
    const elRight = elLeft + el.offsetWidth;
    const viewLeft = strip.scrollLeft;
    const viewRight = viewLeft + strip.clientWidth;
    let next = viewLeft;
    if (elLeft < viewLeft + pad) next = elLeft - pad;
    else if (elRight > viewRight - pad) next = elRight - strip.clientWidth + pad;
    const maxScroll = Math.max(0, strip.scrollWidth - strip.clientWidth);
    next = Math.max(0, Math.min(maxScroll, next));
    if (next === viewLeft) return;
    const behavior = instantScrollRef.current ? "instant" : "smooth";
    instantScrollRef.current = false;
    strip.scrollTo({ left: next, behavior });
  }, [agentDeskIndex]);

  // ── Animation / sleep refs ─────────────────────────────────────────────────

  const walkToDeskIndexRef = useRef((_targetIdx: number, _onArrive?: () => void) => {});
  walkToDeskIndexRef.current = (targetIdx: number, onArrive?: () => void) => {
    if (isWalkingRef.current) return;
    const sourceIdx = agentDeskIndexRef.current;
    if (targetIdx === sourceIdx) {
      onArrive?.();
      return;
    }
    const fromEl = deskRefs.current.get(sourceIdx);
    const toEl   = deskRefs.current.get(targetIdx);
    agentDeskIndexRef.current = targetIdx;
    setAgentDeskIndex(targetIdx);
    if (!fromEl || !toEl) {
      onArrive?.();
      return;
    }
    const fr = fromEl.getBoundingClientRect();
    const tr = toEl.getBoundingClientRect();
    const W = 40;
    isWalkingRef.current = true;
    setIsWalking(true);
    setWalk({
      fromX: fr.left + fr.width / 2 - W / 2,
      toX: tr.left + tr.width / 2 - W / 2,
      fromY: fr.top,
      toY: tr.top,
      dir: tr.left >= fr.left ? 1 : -1,
      visual: lastAvatarVisualRef.current,
    });
    setTimeout(() => {
      setWalk(null);
      setIsWalking(false);
      isWalkingRef.current = false;
      onArrive?.();
    }, 490);
  };

  const moveAgentVisuallyRef = useRef((_targetIdx: number) => {});
  moveAgentVisuallyRef.current = (targetIdx: number) => {
    if (sleepPhaseRef.current !== "awake") return;
    const target = desksRef.current[targetIdx];
    // A new / unassigned desk has no agent of its own. Don't drag the roaming
    // (possibly actively-working) agent over to it — that looks like an existing
    // agent walking off the desk it was working on. Just make the desk primary so
    // its own generic default avatar appears in place, with no walk animation.
    if (!target || "isPending" in target) {
      if (isWalkingRef.current) return;
      agentDeskIndexRef.current = targetIdx;
      setAgentDeskIndex(targetIdx);
      return;
    }
    walkToDeskIndexRef.current(targetIdx);
  };

  const moveToDeskRef = useRef((_i: number, _fromRunningId?: string) => {});
  moveToDeskRef.current = (targetIdx: number, fromRunningId?: string) => {
    walkToDeskIndexRef.current(targetIdx, () => {
      const dest = desksRef.current[targetIdx];
      if (!dest || "isPending" in dest) return;
      const s = dest as Session;
      if (s.is_running === true) return;
      if (s.task_solved) {
        api.sessions.arrive(s.id).catch(() => {});
        setAgentSay("This task's already done ✓");
        setTimeout(() => setAgentSay(null), 2400);
        return;
      }
      if (s.message_count > 0) {
        if (fromRunningId) {
          api.sessions.reassign(fromRunningId, s.id, "Continue.").catch(() => {});
        } else {
          api.sessions.arrive(s.id).catch(() => {});
          api.sessions.resume(s.id, "Continue.", undefined, undefined, reasoningEffort, apiMode).catch(() => {});
        }
      } else {
        api.sessions.arrive(s.id).catch(() => {});
      }
    });
  };

  const doGoToSleepRef = useRef(() => {});
  doGoToSleepRef.current = () => {
    if (sleepPhaseRef.current !== "awake" || isWalkingRef.current) return;

    const finishSleep = () => {
      setWalk(null);
      setIsWalking(false);
      isWalkingRef.current = false;
      sleepPhaseRef.current = "sleeping";
      setSleepPhase("sleeping");
    };

    // All tasks solved or stopped — snap to bed without a walk animation.
    if (!teamHasActiveWork(desksRef.current)) {
      finishSleep();
      return;
    }

    const fromEl = deskRefs.current.get(agentDeskIndexRef.current);
    const bedEl  = bedRef.current;
    if (!fromEl || !bedEl) {
      finishSleep();
      return;
    }
    const fr = fromEl.getBoundingClientRect();
    const br = bedEl.getBoundingClientRect();
    const W = 40;
    isWalkingRef.current = true;
    sleepPhaseRef.current = "walking-to-bed"; setSleepPhase("walking-to-bed");
    setWalk({ fromX: fr.left + fr.width / 2 - W / 2, toX: br.left + br.width / 2 - W / 2, fromY: fr.top, toY: br.top, dir: br.left < fr.left ? -1 : 1 });
    setIsWalking(true);
    setTimeout(finishSleep, 560);
  };

  const doWakeUpRef = useRef<(targetIdx?: number) => void>(() => {});
  doWakeUpRef.current = (targetIdx?: number) => {
    if (sleepPhaseRef.current !== "sleeping") return;
    const idx = targetIdx ?? agentDeskIndexRef.current;
    const target = desksRef.current[idx];
    const shouldWalk = target && deskNeedsRoamingAgent(target);

    const finishWake = () => {
      setWalk(null);
      setIsWalking(false);
      isWalkingRef.current = false;
      sleepPhaseRef.current = "awake";
      setSleepPhase("awake");
    };

    agentDeskIndexRef.current = idx;
    setAgentDeskIndex(idx);

    if (!shouldWalk) {
      finishWake();
      return;
    }

    const toEl  = deskRefs.current.get(idx);
    const bedEl = bedRef.current;
    if (!toEl || !bedEl) {
      finishWake();
      return;
    }
    const tr = toEl.getBoundingClientRect();
    const br = bedEl.getBoundingClientRect();
    const W = 40;
    isWalkingRef.current = true;
    sleepPhaseRef.current = "waking"; setSleepPhase("waking");
    setWalk({ fromX: br.left + br.width / 2 - W / 2, toX: tr.left + tr.width / 2 - W / 2, fromY: br.top, toY: tr.top, dir: tr.left > br.left ? 1 : -1 });
    setIsWalking(true);
    setTimeout(finishWake, 560);
  };

  const wakeAndMoveRef = useRef((_i: number) => {});
  wakeAndMoveRef.current = (targetIdx: number) => {
    const wakeIdx = findPreferredDeskIndex(desksRef.current, targetIdx);
    if (sleepPhaseRef.current !== "sleeping") {
      moveToDeskRef.current(wakeIdx);
      return;
    }
    const targetDesk = desksRef.current[wakeIdx];
    if (targetDesk && !("isPending" in targetDesk)) {
      api.sessions.wake((targetDesk as Session).id).catch(() => {});
    }
    doWakeUpRef.current(wakeIdx);
    // Clicking/highlighting a desk to wake the avatar must NOT auto-resume it —
    // that was sending a surprise "Continue." whenever you focused an idle, slept
    // desk. Only a genuinely live desk follows the avatar here; explicit resume
    // stays with the per-desk Resume button and the bell (which resumes the whole
    // team via its own loop, independent of this path).
    if (deskNeedsRoamingAgent(targetDesk)) {
      setTimeout(() => { moveToDeskRef.current(wakeIdx); }, 600);
    }
  };

  const handleDeskActivityRef = useRef((_i: number) => {});
  handleDeskActivityRef.current = (deskIdx: number) => {
    lastServerActivityRef.current = Date.now();
    lastActiveDeskRef.current = deskIdx;
    if (managerOnPatrolRef.current) return;
    const desk = desksRef.current[deskIdx];
    const session = desk && !("isPending" in desk) ? (desk as Session) : null;
    // An executing desk renders its own stationary avatar (deskShowsAvatar) even
    // when no profile is assigned (default avatar). Treat it like a desk with its
    // own agent here — otherwise every streamed event from a running default-avatar
    // desk walked the roaming figure back to it, yanking it off whatever desk the
    // user had just focused.
    const hasOwnAgent = !!session
      && (!!session.agent || (session.is_running === true && !session.task_solved));

    // Desk activity is REACTIVE (a worker streamed an event / a turn started). It
    // must only reflect who's working visually — never resume/invoke an agent.
    // Agent resumes happen solely on explicit triggers: the bell, a roster drag,
    // the Resume button, or the manager. Calling moveToDesk() here (which issues a
    // "Continue." resume) was the source of spurious wake-ups on desks that have a
    // running worker but no assigned profile.
    if (sleepPhaseRef.current === "sleeping") {
      if (hasOwnAgent) {
        sleepPhaseRef.current = "awake";
        setSleepPhase("awake");
      } else {
        // Real activity arrived for a UI-asleep avatar → wake it visually only.
        doWakeUpRef.current(deskIdx);
      }
      return;
    }

    if (hasOwnAgent) return;

    moveAgentVisuallyRef.current(deskIdx);
  };

  // Idle sleep timer
  useEffect(() => {
    const iv = setInterval(() => {
      const anyActive = desksRef.current.some(isLiveSession);
      if (anyActive) { lastServerActivityRef.current = Date.now(); return; }
      if (sleepPhaseRef.current === "awake" && Date.now() - lastServerActivityRef.current > 15000) {
        doGoToSleepRef.current();
      }
    }, 1000);
    return () => clearInterval(iv);
  }, []);

  // Global interaction listener for this row — wakes + routes agent when sleeping
  useEffect(() => {
    function onInteraction(e: MouseEvent | KeyboardEvent) {
      lastServerActivityRef.current = Date.now();
      if (sleepPhaseRef.current !== "sleeping") return;
      if (e instanceof MouseEvent) {
        const target = e.target as Element;
        // The bell and bed own their own click handlers (wake + resume the whole
        // team). This generic mousedown listener fires BEFORE their onClick, so if
        // we wake here first we flip out of "sleeping" and their resume branch is
        // skipped — the avatar just snaps back with no resume. Leave those (and the
        // manager staging tile) to their dedicated handlers.
        if (bellRef.current?.contains(target)
            || bedRef.current?.contains(target)
            || stagingRef.current?.contains(target)) {
          return;
        }
        for (const [i, deskEl] of deskRefs.current) {
          if (deskEl.contains(target)) {
            wakeAndMoveRef.current(findPreferredDeskIndex(desksRef.current, i));
            return;
          }
        }
      }
      doWakeUpRef.current(findPreferredDeskIndex(desksRef.current, lastActiveDeskRef.current));
    }
    window.addEventListener("mousedown", onInteraction);
    window.addEventListener("keydown",   onInteraction);
    return () => {
      window.removeEventListener("mousedown", onInteraction);
      window.removeEventListener("keydown",   onInteraction);
    };
  }, []);

  function stopAllTeamAgents() {
    desksRef.current.forEach((d) => {
      if ("isPending" in d) return;
      const sid = (d as Session).id;
      api.sessions.interrupt(sid).catch(() => {});
      // Pause server-side too, so background auto-continue / manager patrol can't
      // re-resume them. Cleared on explicit wake/resume.
      api.sessions.sleep(sid).catch(() => {});
      onSessionInterrupt?.(sid);
    });
  }

  // Wake the WHOLE team and resume every non-solved desk that has work — not just
  // the focused one. Shared by the bell and the (sleeping) bed, both of which
  // promise "Wake all non-solved tasks". Each resumed desk shows its own working
  // avatar; the roaming figure walks to the most relevant one.
  function wakeAndResumeAll() {
    setBedSelected(false);
    const resumable = desksRef.current.filter(deskResumable) as Session[];
    doWakeUpRef.current(findPreferredDeskIndex(desksRef.current, lastActiveDeskRef.current));
    // Await the wakes before resuming so the server has cleared each desk's
    // sleeping flag — otherwise resume races it and gets a 423 (and the desk
    // is re-seated but never resumed). We deliberately don't gate on the
    // frontend's `is_running`: right after a bed-stop the session list can
    // still report a stale `true`, which would skip the resume entirely. The
    // server's own 409 (already-running) check dedupes any real double-resume.
    void (async () => {
      await Promise.all(
        desksRef.current
          .filter((d) => !("isPending" in d))
          .map((d) => api.sessions.wake((d as Session).id).catch(() => {})),
      );
      for (const s of resumable) {
        api.sessions.resume(s.id, "Continue.", undefined, undefined, reasoningEffort, apiMode).catch(() => {});
      }
    })();
    lastServerActivityRef.current = Date.now();
  }

  function handleBedClick() {
    if (sleepPhaseRef.current === "sleeping") { wakeAndResumeAll(); return; }
    setBedSelected(false);
    stopAllTeamAgents();
    doGoToSleepRef.current();
  }

  function handleBellClick() {
    playBell(bellSound);
    setBellRinging(true);
    setTimeout(() => setBellRinging(false), 1800);
    if (sleepPhaseRef.current === "sleeping") {
      wakeAndResumeAll();
    } else {
      stopAllTeamAgents();
      doGoToSleepRef.current();
    }
    lastServerActivityRef.current = Date.now();
  }

  function focusDeskAtIndex(targetIdx: number, deskId: string) {
    lastActiveDeskRef.current = targetIdx;
    const desk = desksRef.current[targetIdx];
    if (sleepPhaseRef.current === "sleeping") {
      wakeAndMoveRef.current(targetIdx);
    } else if (bedSelected) {
      setBedSelected(false);
      wakeAndMoveRef.current(targetIdx);
    } else {
      // Focusing/highlighting a desk must not trigger a walk animation — snap
      // the avatar to it instead. A walk here read as the agent leaving the
      // desk it was actively working on. Reactive desk activity
      // (handleDeskActivity) still animates a walk.
      if (!isWalkingRef.current) {
        agentDeskIndexRef.current = targetIdx;
        setAgentDeskIndex(targetIdx);
      }
    }
    onDeskFocus?.(deskId);
    if (desk && "isPending" in desk) {
      onActivePendingDeskChange?.(deskId);
    }
  }

  function handleAgentClick() { setAgentSelected(s => !s); }

  function handleDeskSelect(targetIdx: number) {
    if (bedSelected) { setBedSelected(false); wakeAndMoveRef.current(targetIdx); return; }
    if (sleepPhaseRef.current === "sleeping") return;  // sleeping: only bell/Resume can wake
    if (!agentSelected || targetIdx === agentDeskIndexRef.current) return;

    let fromRunningId: string | undefined;
    const currentDesk = desksRef.current[agentDeskIndexRef.current];
    if (currentDesk && !("isPending" in currentDesk)) {
      const s = currentDesk as Session;
      if (s.is_running === true) {
        fromRunningId = s.id;
        api.sessions.interrupt(s.id).catch(() => {});
        onSessionInterrupt?.(s.id);
      }
    }
    moveToDeskRef.current(targetIdx, fromRunningId);
    setAgentSelected(false);
  }

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const pct = el.scrollLeft / (el.scrollWidth - el.clientWidth);
    setScrollPct(isNaN(pct) ? 0 : pct);
  }

  const accentColor = TEAM_COLOR_ACCENT[team.color] ?? TEAM_COLOR_ACCENT.blue;
  const bgTint      = TEAM_COLOR_BG[team.color]     ?? TEAM_COLOR_BG.blue;
  const chrome      = sceneFloorChrome(scene ?? DEFAULT_SCENE);

  // Tell the backend which live desks belong to this team so the File Repo is
  // copied into their workspace (survives server restarts; picks up uploads).
  const teamSessionKey = desks
    .filter((d) => !("isPending" in d))
    .map((d) => d.id)
    .join(",");
  useEffect(() => {
    if (!teamSessionKey) return;
    api.teams.register(team.id, teamSessionKey.split(",")).catch(() => {});
  }, [team.id, teamSessionKey]);

  return (
    <>
      {/* Team row: fixed height; panels may extend past the row (overflow visible). */}
      <div ref={setPanelRoot} style={{ position: "relative", height: TEAM_ROW_HEIGHT, overflow: "visible", background: "var(--floor)" }}>
        <TeamRowPanelContext.Provider value={{ root: panelRoot, height: TEAM_ROW_HEIGHT }}>
        <SceneBackground scene={scene ?? DEFAULT_SCENE} />

        {/* Subtle team color tint overlay */}
        <div style={{ position: "absolute", inset: 0, background: bgTint, zIndex: 1, pointerEvents: "none" }} />

        {/* Team chrome — aligned column shared by every team row */}
        <div style={{
          position: "absolute", top: 6, left: 8, zIndex: 10,
          width: TEAM_CHROME_WIDTH,
          display: "flex", flexDirection: "column", gap: 8,
          pointerEvents: "auto",
        }}>
          {/* Team name (left) · bed/bell (right); manager + file repo under name */}
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <div style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background: accentColor, flexShrink: 0,
                  boxShadow: `0 0 6px ${accentColor}`,
                }} />
                <div style={{ color: accentColor, minWidth: 0 }}>
                  <TeamNameLabel label={teamLabel} onRename={onTeamRename} chrome={chrome} />
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                {onSceneChange && (
                  <select
                    value={scene ?? DEFAULT_SCENE}
                    onChange={(e) => onSceneChange(e.target.value)}
                    title="Background for this team"
                    style={{
                      height: 20, borderRadius: 4, border: `1px solid ${chrome.controlBorder}`,
                      background: chrome.controlBg, color: chrome.controlColor,
                      fontSize: 11, lineHeight: 1, cursor: "pointer", padding: "0 4px",
                    }}
                  >
                    {SCENES.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                )}
                {onDeleteTeam && (
                  <button
                    onClick={() => {
                      if (window.confirm(`Delete "${teamLabel}" and all its desks?`)) {
                        onDeleteTeam();
                      }
                    }}
                    title="Delete this team"
                    style={{
                      width: 20, height: 18, borderRadius: 4, border: `1px solid ${chrome.controlBorder}`,
                      background: chrome.controlBg, color: chrome.controlColor,
                      fontSize: 12, lineHeight: 1, cursor: "pointer", padding: 0,
                      display: "flex", alignItems: "center", justifyContent: "center",
                    }}
                  >×</button>
                )}
              </div>
              <div style={{
                display: "flex", flexDirection: "column", alignItems: "flex-start",
                gap: 12, marginTop: 4,
              }}>
                {(showManager ?? true) && (
                  <ManagerStagingArea
                    stagingRef={stagingRef}
                    onPatrol={managerOnPatrol}
                    onClick={() => { if (!managerOnPatrol) onDeskAskManager?.("__manual__"); }}
                    chrome={chrome}
                    agents={agents ?? []}
                  />
                )}
                <TeamFileRepo
                  teamId={team.id}
                  accentColor={accentColor}
                  chrome={chrome}
                  onPreview={onPreview}
                  tileWidth={TEAM_CHROME_TILE_WIDTH}
                  tileMinHeight={TEAM_CHROME_TILE_MIN_HEIGHT}
                />
              </div>
            </div>
            <div style={{
              display: "flex", alignItems: "flex-start", gap: 6, flexShrink: 0,
              paddingTop: 2, marginLeft: 32,
            }}>
              <Bed isSleeping={sleepPhase === "sleeping"} selected={bedSelected} bedRef={bedRef} onClick={handleBedClick} chrome={chrome} />
              <div ref={bellRef} style={{ paddingTop: 10 }}>
                <BellButton onClick={handleBellClick} glowing={bellRinging} />
              </div>
            </div>
          </div>
        </div>

        <style>{`
          @keyframes slpZ     { 0%,100%{opacity:0.15;transform:translateY(0)} 50%{opacity:1;transform:translateY(-5px)} }
          @keyframes bell-ring { 0%{opacity:0.8;transform:scale(1)} 100%{opacity:0;transform:scale(2)} }
          @keyframes fnbob    { 0%,100%{transform:translateX(-50%) translateY(0)} 50%{transform:translateX(-50%) translateY(-3px)} }
        `}</style>

        {/* Desk strip */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          style={{
            position: "absolute", inset: 0,
            overflowX: "auto", overflowY: "auto",
            display: "flex", alignItems: "flex-start",
            paddingTop: 72, paddingLeft: TEAM_CHROME_WIDTH + 24, paddingBottom: 24, paddingRight: 40,
            gap: 192, scrollbarWidth: "thin",
            visibility: desksVisible ? "visible" : "hidden",
          }}
        >
          {desks.map((desk, i) => {
            const isPending = "isPending" in desk;
            const session = isPending ? null : (desk as Session);
            const isPrimary   = i === agentDeskIndex;
            const pendingAssignment = isPending ? pendingAssignments?.[desk.id] : undefined;
            const deskCfg = deskConfigsById?.[desk.id];
            const profileVis = resolveDeskProfileVisual({
              session: isPending ? null : session,
              deskCfg,
              pendingAssignment,
              agents,
              getAvatarPref: (id) => avatars.get(id),
            });
            const effectiveAgentId = profileVis.agentId;
            const deskColor = profileVis.color;
            const executing = !isPending && session?.is_running === true && !session.task_solved;
            // An executing desk ALWAYS shows its own stationary avatar — an agent is
            // working there, so it must appear on every running task (including when
            // several desks run the same profile). Only the roaming/focus figure is
            // hidden mid-walk (the WalkingAgent animates it), never the working ones.
            const showAvatar  = sleepPhase === "awake"
              && deskShowsAvatar(isPending, isPrimary, executing)
              && (executing || !isWalking);
            if (showAvatar) {
              lastAvatarVisualRef.current = {
                agentId: profileVis.agentId,
                color: profileVis.color,
                archetype: profileVis.archetype,
                isPrototype: profileVis.isPrototype,
                cloneFrom: profileVis.cloneFrom,
              };
            }
            const canDrag     = showAvatar && !!session?.agent && !executing;
            const isDeskSelected = desk.id === selectedDeskId;
            const showBubble  = !isWalking && isPending && isPrimary && !agentSay;
            const bubbleText  = "What would you like me to do?";
            const dropHighlight = desk.id === deskDropHoverId;
            return (
              <div
                key={desk.id}
                ref={(el) => { if (el) deskRefs.current.set(i, el); else deskRefs.current.delete(i); }}
                data-desk-id={desk.id}
                data-desk-pending={isPending ? "1" : "0"}
                style={{ position: "relative", flexShrink: 0 }}
              >
                <div style={{
                  position: "relative", display: "flex", justifyContent: "center",
                  marginBottom: 4, height: 80, alignItems: "flex-end",
                  opacity: showAvatar ? 1 : 0,
                  transition: isWalking ? "none" : "opacity 0.3s ease",
                  pointerEvents: showAvatar ? "auto" : "none",
                }}>
                  {isPrimary && agentSay
                    ? <SpeechBubble text={agentSay} />
                    : showBubble && <SpeechBubble text={bubbleText} />}
                  <div style={{ position: "relative", display: "flex", alignItems: "flex-end" }}>
                    <AgentFigure
                      agentId={profileVis.isDefault ? undefined : profileVis.agentId || undefined}
                      color={profileVis.color}
                      archetype={profileVis.archetype}
                      isPrototype={profileVis.isPrototype}
                      cloneFrom={profileVis.cloneFrom}
                      state={agentStateForDesk(desk)}
                      selected={isDeskSelected || (agentSelected && isPrimary && profileVis.isDefault)}
                      onClick={() => {
                        focusDeskAtIndex(i, desk.id);
                        onAvatarClick?.(desk.id);
                      }}
                      onMouseDown={canDrag
                        ? (e) => { e.preventDefault(); e.stopPropagation();
                            onAgentDragStart?.(e, session!.id, session!.agent!, deskColor, agentStateForDesk(desk)); }
                        : undefined}
                    />
                    {isDeskSelected && (
                      // Absolutely positioned to the avatar's right so opening
                      // settings never nudges the centered avatar sideways.
                      <div style={{ position: "absolute", left: "100%", bottom: 8, marginLeft: 6 }}>
                        <PixelSettingsButton
                          active={settingsDesk?.deskId === desk.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            const anchor = deskRefs.current.get(i);
                            if (!anchor) return;
                            setSettingsDesk((cur) => (cur?.deskId === desk.id ? null : { deskId: desk.id, anchor }));
                          }}
                        />
                      </div>
                    )}
                  </div>
                </div>

                {isPending ? (
                  <div style={{
                    outline: isDeskSelected
                      ? "2px solid var(--accent2)"
                      : dropHighlight
                      ? "3px dashed var(--accent2)"
                      : undefined,
                    outlineOffset: dropHighlight ? 6 : 4,
                    borderRadius: 10,
                    boxShadow: dropHighlight ? "0 0 16px rgba(100,200,255,0.35)" : undefined,
                    transition: "outline-color 0.12s ease, box-shadow 0.12s ease",
                  }}>
                  <PendingTaskDesk
                    deskIndex={i}
                    scene={scene ?? DEFAULT_SCENE}
                    isActive={i === agentDeskIndex}
                    dropHighlight={dropHighlight}
                    initialMsg={pendingTexts?.[desk.id]}
                    assignment={pendingAssignment}
                    onStart={(msg, agentId, images, anchor) => onDeskStart(desk.id, msg, agentId, images, anchor)}
                    onSelect={() => focusDeskAtIndex(i, desk.id)}
                    onClose={() => onDeskClose(desk.id)}
                    onMsgChange={(msg) => onPendingMsgChange?.(desk.id, msg)}
                  />
                  </div>
                ) : (
                  <div style={{
                    outline: dropHighlight ? "3px dashed var(--accent2)" : undefined,
                    outlineOffset: 6,
                    borderRadius: 10,
                    boxShadow: dropHighlight ? "0 0 16px rgba(100,200,255,0.35)" : undefined,
                  }}>
                  <TaskDesk
                    session={session!}
                    scene={scene ?? DEFAULT_SCENE}
                    isActive={i === agentDeskIndex}
                    searchMatch={searchMatchIds?.has(session!.id)}
                    index={i}
                    profileLabel={profileVis.label}
                    profileColor={profileVis.color}
                    profileModel={profileVis.model}
                    autoExpand={session!.id === justStartedId}
                    openAnchor={session!.id === justStartedId ? justStartedAnchor : null}
                    workspacePath={workspacePaths?.[session!.id]}
                    taskContent={taskContents?.[session!.id]}
                    taskImages={taskImages?.[session!.id]}
                    verbose={verbose}
                    reasoningEffort={reasoningEffort}
                    apiMode={apiMode}
                    onPreview={onPreview}
                    panelZIndex={deskPanelZ?.[desk.id]}
                    onPanelActivate={() => onDeskPanelActivate?.(desk.id)}
                    onSelect={() => {
                      if (agentSelected) {
                        handleDeskSelect(i);
                        onDeskFocus?.(desk.id);
                      } else {
                        focusDeskAtIndex(i, desk.id);
                      }
                    }}
                    onFocus={() => {
                      if (!agentSelected) focusDeskAtIndex(i, desk.id);
                    }}
                    onOpen={() => {
                      onDeskPanelActivate?.(desk.id);
                      if (!agentSelected) focusDeskAtIndex(i, desk.id);
                    }}
                    deskFocused={isDeskSelected}
                    onClose={() => onDeskClose(desk.id)}
                    onAutoExpanded={onJustStartedConsumed}
                    onActivity={() => handleDeskActivityRef.current(i)}
                    onInterrupt={onSessionInterrupt}
                    onAskManager={showManager ? () => onDeskAskManager?.(session!.id) : undefined}
                  />
                  </div>
                )}
              </div>
            );
          })}

          <AddDeskButton onClick={onAddDesk} />
        </div>

        {/* Bed-select / agent-select hint */}
        {(agentSelected || bedSelected) && (
          <div style={{
            position: "absolute", bottom: 16, left: "50%", transform: "translateX(-50%)",
            background: "rgba(255,255,255,0.12)", backdropFilter: "blur(6px)",
            border: "1px solid rgba(255,255,255,0.2)", borderRadius: 20, padding: "6px 16px",
            fontSize: 13, color: chrome.label, pointerEvents: "none", zIndex: 20, whiteSpace: "nowrap",
          }}>
            {bedSelected ? "Click a desk to assign agent there" : "Click any desk to move the agent there"}
          </div>
        )}
        </TeamRowPanelContext.Provider>
      </div>

      {/* Minimap */}
      {desks.length > 3 && (
        <div style={{ height: 4, background: "var(--bg2)", position: "relative", overflow: "hidden" }}>
          <div style={{
            position: "absolute", top: 0, height: "100%",
            background: accentColor, borderRadius: 2,
            width: `${Math.max(10, 100 / desks.length)}%`,
            left: `${scrollPct * (100 - Math.max(10, 100 / desks.length))}%`,
            transition: "left 0.1s",
          }} />
        </div>
      )}

      {/* Team manager patrol overlay (fixed position, scoped by scrollRef coords) */}
      <FloorManager
        desks={desks}
        deskRefs={deskRefs}
        scrollRef={scrollRef}
        stagingRef={stagingRef}
        enabled={(showManager ?? true) && sleepPhase === "awake"}
        patrolIntervalSec={managerPatrolIntervalSec ?? 60}
        idleGraceSec={managerIdleGraceSec ?? 60}
        reasoningEffort={reasoningEffort}
        apiMode={apiMode}
        askManagerDeskId={askManagerDeskId}
        onAskManagerDone={onAskManagerDone}
        onPatrolChange={setManagerOnPatrol}
      />

      {/* Walking agent overlay */}
      {walk && <WalkingAgent {...walk} />}

      {/* Per-desk agent-settings subpage (gear → opens to the right of the desk) */}
      {settingsDesk && deskConfigsById?.[settingsDesk.deskId] && (() => {
        const cfg = deskConfigsById[settingsDesk.deskId];
        const deskItem = desks.find((d) => d.id === settingsDesk.deskId) ?? null;
        return (
          <DeskSettingsPanel
            config={cfg}
            agents={agents ?? []}
            toolsets={toolsets ?? []}
            anchor={settingsDesk.anchor}
            panelRoot={panelRoot}
            locked={deskIsRunning(deskItem)}
            reasoningValue={reasoningValue ?? "medium"}
            reasoningOptions={reasoningOptions ?? []}
            reasoningDisabled={isVllmBackend(cfg.baseUrl)}
            onProfileChange={(agentId) => onDeskConfigProfileChange?.(settingsDesk.deskId, agentId)}
            onModelChange={(model) => onDeskConfigModelChange?.(settingsDesk.deskId, model)}
            onToolsChange={(preset, enabled) => onDeskConfigToolsChange?.(settingsDesk.deskId, preset, enabled)}
            onReasoningChange={(v) => onDeskConfigReasoningChange?.(v)}
            onClose={() => setSettingsDesk(null)}
          />
        );
      })()}
    </>
  );
}
