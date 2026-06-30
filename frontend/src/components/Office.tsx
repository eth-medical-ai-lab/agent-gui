import React from "react";
import type { AgentProfile, DeskItem, FilePreviewData, PendingAssignment, ReasoningEffort, Session, Team, ToolPresetId, ToolsetMeta } from "../types";
import type { DeskConfigView } from "../deskConfig";
import { TeamRow } from "./TeamRow";

// Extra scroll room below the last team row. A desk panel is anchored *below* its
// row in viewport space (portaled to document.body, position: fixed), so on the
// bottom team row it lands past the floor's scrollable content. Without slack here
// there's nowhere to scroll it, so opening that desk shows "no window extending
// down". Sized to clear a default-height desk panel.
const FLOOR_BOTTOM_RESERVE = 480;

interface Props {
  teams: Team[];
  searchMatchIds?: Set<string>;
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
  showManager?: boolean;
  managerPatrolIntervalSec?: number;
  managerIdleGraceSec?: number;
  agents?: AgentProfile[];
  pendingAssignments?: Record<string, PendingAssignment>;
  activePendingDeskId?: string | null;
  deskDropHoverId?: string | null;
  askManagerByTeamId?: Record<string, string | null>;
  onAskManagerDone?: (teamId: string) => void;
  onDeleteTeam?: (teamId: string) => void;
  onPreview: (data: FilePreviewData) => void;
  deskPanelZ?: Record<string, number>;
  onDeskPanelActivate?: (deskId: string) => void;
  onDeskStart: (deskId: string, msg: string, agentId: string, images?: { name: string; url: string }[], anchor?: { top: number; left: number }) => Promise<void>;
  onDeskClose: (deskId: string) => void;
  onAddDesk: (teamId: string) => void;
  onAddTeam: () => void;
  onTeamSceneChange?: (teamId: string, sceneId: string) => void;
  onTeamRename?: (teamId: string, name: string) => void;
  onSessionInterrupt?: (id: string) => void;
  onAssignAgentToDesk?: (deskId: string, agentId: string) => void;
  onAgentDragStart?: (
    e: React.MouseEvent, sessionId: string, agentId: string,
    color?: string, state?: "idle" | "working" | "thinking",
  ) => void;
  onPendingMsgChange?: (id: string, msg: string) => void;
  onPendingAssignmentPatch?: (deskId: string, patch: Partial<PendingAssignment>) => void;
  onActivePendingDeskChange?: (deskId: string | null) => void;
  onDeskFocus?: (deskId: string) => void;
  focusedDeskId?: string | null;
  selectedDeskId?: string | null;
  deskConfigsById?: Record<string, DeskConfigView>;
  onAvatarClick?: (deskId: string) => void;
  onDeskAskManager?: (teamId: string, sessionId: string) => void;
  // Per-desk agent-settings panel (gear next to the avatar).
  toolsets?: ToolsetMeta[];
  reasoningValue?: ReasoningEffort;
  reasoningOptions?: { value: ReasoningEffort; label: string }[];
  onDeskConfigProfileChange?: (deskId: string, agentId: string) => void;
  onDeskConfigModelChange?: (deskId: string, model: string) => void;
  onDeskConfigToolsChange?: (deskId: string, preset: ToolPresetId, enabled: string[]) => void;
  onDeskConfigReasoningChange?: (v: ReasoningEffort) => void;
}

function WallClock() {
  const [time, setTime] = React.useState(new Date());
  React.useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const s = time.getSeconds() * 6;
  const m = time.getMinutes() * 6 + s / 60;
  const h = (time.getHours() % 12) * 30 + m / 12;
  return (
    <div style={{
      position: "sticky", top: 10, right: 16,
      width: 44, height: 44, marginLeft: "auto", marginRight: 16,
      background: "rgba(15,15,40,0.7)", borderRadius: "50%",
      border: "1px solid rgba(255,255,255,0.08)",
      zIndex: 20, flexShrink: 0, pointerEvents: "none",
    }}>
      <svg viewBox="0 0 44 44" width="44" height="44">
        {[0,30,60,90,120,150,180,210,240,270,300,330].map(d => (
          <line key={d} x1="22" y1="5" x2="22" y2={d%90===0?8:7}
            stroke="rgba(255,255,255,0.35)" strokeWidth={d%90===0?1.5:0.8}
            transform={`rotate(${d} 22 22)`} />
        ))}
        <line x1="22" y1="22" x2="22" y2="12" stroke="var(--text)" strokeWidth="2.2" strokeLinecap="round" transform={`rotate(${h} 22 22)`} />
        <line x1="22" y1="22" x2="22" y2="10" stroke="var(--text)" strokeWidth="1.4" strokeLinecap="round" transform={`rotate(${m} 22 22)`} />
        <line x1="22" y1="24" x2="22" y2="9"  stroke="var(--accent)" strokeWidth="1" strokeLinecap="round" transform={`rotate(${s} 22 22)`} />
        <circle cx="22" cy="22" r="2" fill="var(--accent)" />
      </svg>
    </div>
  );
}

function AddTeamButton({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = React.useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        height: 72, display: "flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer",
        border: `1px dashed rgba(255,255,255,${hover ? 0.25 : 0.08})`,
        borderRadius: 8, margin: "48px 24px 8px",
        transition: "border-color 0.2s, opacity 0.2s",
        opacity: hover ? 0.85 : 0.45,
        gap: 10,
      }}
    >
      <span style={{ fontSize: 24, color: "var(--text-dim)", lineHeight: 1, userSelect: "none" }}>+</span>
      <span style={{
        fontSize: 11, fontWeight: 600, color: "var(--text-dim)",
        letterSpacing: "0.05em", textTransform: "uppercase", userSelect: "none",
      }}>New Team</span>
    </div>
  );
}

export function Office({
  teams, searchMatchIds, justStartedId, justStartedAnchor, onJustStartedConsumed, workspacePaths, taskContents, taskImages, pendingTexts,
  verbose, reasoningEffort, apiMode, bellSound, scene, showManager,
  managerPatrolIntervalSec, managerIdleGraceSec, agents,
  pendingAssignments, activePendingDeskId, deskDropHoverId, askManagerByTeamId, onAskManagerDone,
  onPreview, onDeskStart, onDeskClose,
  deskPanelZ, onDeskPanelActivate,
  onAddDesk, onAddTeam, onDeleteTeam, onSessionInterrupt, onAssignAgentToDesk,
  onPendingMsgChange, onPendingAssignmentPatch, onActivePendingDeskChange, onDeskFocus, focusedDeskId, selectedDeskId,
  deskConfigsById, onAvatarClick, onDeskAskManager, onAgentDragStart,
  onTeamSceneChange, onTeamRename,
  toolsets, reasoningValue, reasoningOptions,
  onDeskConfigProfileChange, onDeskConfigModelChange, onDeskConfigToolsChange, onDeskConfigReasoningChange,
}: Props) {
  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column",
      overflow: "hidden", background: "var(--floor)", position: "relative",
    }}>
      <div style={{
        position: "absolute", top: 10, right: 16, zIndex: 100,
        pointerEvents: "none",
      }}>
        <WallClock />
      </div>

      <div data-floor-scroll style={{ flex: 1, overflowY: "auto", overflowX: "hidden", paddingBottom: FLOOR_BOTTOM_RESERVE }}>
        {teams.map((team, idx) => (
          <TeamRow
            key={team.id}
            team={team}
            teamIndex={idx}
            searchMatchIds={searchMatchIds}
            onDeleteTeam={teams.length > 1 ? () => onDeleteTeam?.(team.id) : undefined}
            justStartedId={justStartedId}
            justStartedAnchor={justStartedAnchor}
            onJustStartedConsumed={onJustStartedConsumed}
            workspacePaths={workspacePaths}
            taskContents={taskContents}
            taskImages={taskImages}
            pendingTexts={pendingTexts}
            verbose={verbose}
            reasoningEffort={reasoningEffort}
            apiMode={apiMode}
            bellSound={bellSound}
            scene={team.scene ?? scene}
            onSceneChange={onTeamSceneChange ? (id) => onTeamSceneChange(team.id, id) : undefined}
            onTeamRename={onTeamRename ? (name) => onTeamRename(team.id, name) : undefined}
            showManager={showManager}
            managerPatrolIntervalSec={managerPatrolIntervalSec}
            managerIdleGraceSec={managerIdleGraceSec}
            agents={agents}
            pendingAssignments={pendingAssignments}
            activePendingDeskId={activePendingDeskId}
            deskDropHoverId={deskDropHoverId}
            askManagerDeskId={askManagerByTeamId?.[team.id] ?? null}
            onAskManagerDone={() => onAskManagerDone?.(team.id)}
            onPreview={onPreview}
            deskPanelZ={deskPanelZ}
            onDeskPanelActivate={onDeskPanelActivate}
            onDeskStart={onDeskStart}
            onDeskClose={onDeskClose}
            onAddDesk={() => onAddDesk(team.id)}
            onSessionInterrupt={onSessionInterrupt}
            onPendingMsgChange={onPendingMsgChange}
            onPendingAssignmentPatch={onPendingAssignmentPatch}
            onActivePendingDeskChange={onActivePendingDeskChange}
            onDeskFocus={onDeskFocus}
            focusedDeskId={focusedDeskId}
            selectedDeskId={selectedDeskId}
            deskConfigsById={deskConfigsById}
            onAvatarClick={onAvatarClick}
            onDeskAskManager={(sid) => onDeskAskManager?.(team.id, sid)}
            onAgentDragStart={onAgentDragStart}
            toolsets={toolsets}
            reasoningValue={reasoningValue}
            reasoningOptions={reasoningOptions}
            onDeskConfigProfileChange={onDeskConfigProfileChange}
            onDeskConfigModelChange={onDeskConfigModelChange}
            onDeskConfigToolsChange={onDeskConfigToolsChange}
            onDeskConfigReasoningChange={onDeskConfigReasoningChange}
          />
        ))}

        <AddTeamButton onClick={onAddTeam} />
      </div>
    </div>
  );
}
