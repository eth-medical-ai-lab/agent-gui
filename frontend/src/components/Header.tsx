import { useState } from "react";
import { SettingsMenu } from "./SettingsMenu";
import { SnapshotMenu } from "./SnapshotMenu";
import { LoadDeskMenu } from "./LoadDeskMenu";
import { AgentRosterMenu } from "./AgentRosterMenu";
import { DeskContextBar } from "./DeskContextBar";
import { ReasoningEffortControl } from "./ReasoningEffortControl";
import { isVllmBackend } from "../backendKind";
import type { DeskConfigView } from "../deskConfig";
import type { RosterLayout } from "../rosterLayout";
import type { AgentProfile, ReasoningEffort, Session, Team, ToolPresetId, ToolsetMeta } from "../types";

interface Props {
  teams: Team[];
  sessions: Session[];
  sessionCount: number;
  activeCount: number;
  bellSound: string;
  scene: string;
  showManager: boolean;
  managerPatrolIntervalSec: number;
  managerIdleGraceSec: number;
  agents: AgentProfile[];
  rosterAgents: AgentProfile[];
  toolsets: ToolsetMeta[];
  defaultModel?: string;
  selectedDeskId: string | null;
  deskConfig: DeskConfigView | null;
  deskConfigLocked: boolean;
  reasoningEffort: ReasoningEffort;
  reasoningOptions: { value: ReasoningEffort; label: string }[];
  onDeskProfileChange: (agentId: string) => void;
  onDeskModelChange: (model: string) => void;
  onDeskToolsChange: (preset: ToolPresetId, enabled: string[]) => void;
  onReasoningChange: (v: ReasoningEffort) => void;
  rosterOpen: boolean;
  onRosterOpenChange: (open: boolean) => void;
  rosterRef: React.RefObject<HTMLDivElement | null>;
  rosterDragActive: boolean;
  rosterDropHighlight: boolean;
  rosterLayout: RosterLayout;
  rosterSectionDropHoverId?: string | null;
  onRosterAgentDragStart?: (e: React.MouseEvent, agentId: string, color?: string) => void;
  onAgentEdit?: (agent: AgentProfile) => void;
  onDefaultEdit?: () => void;
  onCreateAgent?: () => void;
  onSearch: (q: string) => void;
  searchStats?: { onFloor: number; total: number } | null;
  onBellSoundChange: (id: string) => void;
  onSceneChange: (id: string) => void;
  onShowManagerChange: (v: boolean) => void;
  onManagerPatrolIntervalChange: (sec: number) => void;
  onManagerIdleGraceChange: (sec: number) => void;
  onReset: () => void;
  onLoadSnapshot: () => void;
  onLoadDesk?: (file: File) => void;
  onLoadSavedDesk?: (filename: string) => void;
  codeTheme: import("./FilePreview").CodeThemeId;
  onCodeThemeChange: (id: import("./FilePreview").CodeThemeId) => void;
  dockerPersist: boolean;
  onDockerPersistChange: (v: boolean) => void;
  verbose: boolean;
  onVerboseChange: (v: boolean) => void;
}

export function Header({
  teams, sessions, sessionCount, activeCount,
  bellSound, scene, showManager, managerPatrolIntervalSec, managerIdleGraceSec,
  agents, rosterAgents, toolsets, defaultModel,
  selectedDeskId, deskConfig, deskConfigLocked,
  reasoningEffort, reasoningOptions,
  onDeskProfileChange, onDeskModelChange, onDeskToolsChange, onReasoningChange,
  rosterOpen, onRosterOpenChange, rosterRef, rosterDragActive, rosterDropHighlight,
  rosterLayout, rosterSectionDropHoverId,
  onRosterAgentDragStart, onAgentEdit, onDefaultEdit, onCreateAgent,
  onSearch, searchStats,
  onBellSoundChange, onSceneChange, onShowManagerChange,
  onManagerPatrolIntervalChange, onManagerIdleGraceChange,
  onReset, onLoadSnapshot, onLoadDesk, onLoadSavedDesk, codeTheme, onCodeThemeChange,
  dockerPersist, onDockerPersistChange, verbose, onVerboseChange,
}: Props) {
  const [logoOk, setLogoOk] = useState(true);

  const reasoningDisabled = deskConfig ? isVllmBackend(deskConfig.baseUrl) : true;
  const showReasoning = Boolean(
    deskConfig && !reasoningDisabled && reasoningOptions.length > 0,
  );

  return (
    <div style={{
      background: "var(--bg2)",
      borderBottom: "1px solid var(--card-border)",
      display: "flex",
      alignItems: "center",
      padding: "8px 16px",
      gap: 12,
      flexShrink: 0,
      zIndex: 200,
      minHeight: 56,
      width: "100%",
      boxSizing: "border-box",
    }}>
      {logoOk
        ? <img src="/full-logo.png" alt="Agent GUI" height={40}
            style={{ display: "block", flexShrink: 0 }}
            onError={() => setLogoOk(false)} />
        : <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <span style={{ fontSize: 20 }}>🏛️</span>
            <span style={{ fontSize: 15, fontWeight: 700 }}>Agent</span>
          </div>}

      <div style={{ width: 1, height: 28, background: "var(--card-border)", flexShrink: 0 }} />

      {/* Task counts — replaces the old "Agent online" pill, which only mirrored
          a periodic LLM-endpoint poll and was often stale. These come straight
          from the session list, so they're always current. */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6, flexShrink: 0,
        padding: "3px 8px", background: "#121828", borderRadius: 6, border: "1px solid #2a3558",
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: activeCount > 0 ? "var(--green)" : "var(--text-dim)",
          ...(activeCount > 0 ? {
            boxShadow: "0 0 6px var(--green)",
            animation: "blink 1.5s ease-in-out infinite",
          } : {}),
        }} />
        <span style={{ fontSize: 10, color: "var(--text-dim)", letterSpacing: 0.4 }}>TASKS</span>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{sessionCount}</span>
        <span style={{ fontSize: 10, color: "var(--card-border)" }}>|</span>
        <span style={{
          fontSize: 12, whiteSpace: "nowrap",
          color: activeCount > 0 ? "var(--green)" : "var(--text-dim)",
        }}>
          {activeCount} ongoing
        </span>
      </div>

      <div style={{ width: 1, height: 28, background: "var(--card-border)", flexShrink: 0 }} />

      {/* Desk config — spread evenly across the bar center */}
      <div style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-evenly",
        minWidth: 0,
        gap: 16,
        opacity: deskConfigLocked ? 0.45 : 1,
        pointerEvents: deskConfigLocked ? "none" : "auto",
      }}>
        {selectedDeskId && deskConfig ? (
          <>
            <DeskContextBar
              config={deskConfig}
              agents={agents}
              toolsets={toolsets}
              bare
              spread
              showLabels
              showAdvanced={false}
              profileReadOnly
              modelReadOnly
              toolsReadOnly
              onProfileChange={onDeskProfileChange}
              onModelChange={onDeskModelChange}
              onToolsChange={onDeskToolsChange}
            />
            {showReasoning && (
              <div style={{ flexShrink: 0 }}>
                <ReasoningEffortControl
                  header
                  readOnly
                  value={reasoningEffort}
                  options={reasoningOptions}
                  onChange={onReasoningChange}
                />
              </div>
            )}
          </>
        ) : (
          <span style={{ fontSize: 11, color: "var(--text-dim)", fontStyle: "italic" }}>
            Select a desk — click its avatar to choose a profile
          </span>
        )}
      </div>

      <div style={{ width: 1, height: 28, background: "var(--card-border)", flexShrink: 0 }} />

      {/* Right actions */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <AgentRosterMenu
          agents={rosterAgents}
          defaultModel={defaultModel}
          open={rosterOpen}
          onOpenChange={onRosterOpenChange}
          rosterRef={rosterRef}
          dragActive={rosterDragActive}
          dropHighlight={rosterDropHighlight}
          rosterLayout={rosterLayout}
          sectionDropHoverId={rosterSectionDropHoverId}
          onAgentDragStart={onRosterAgentDragStart}
          onAgentEdit={onAgentEdit}
          onDefaultEdit={onDefaultEdit}
          onCreateAgent={onCreateAgent}
        />

        <button
          type="button"
          onClick={onReset}
          title="Reset workbench"
          style={{
            height: 28, padding: "0 8px",
            background: "#121828", border: "1px solid #2a3558",
            borderRadius: 6, color: "var(--text-dim)", fontSize: 10, cursor: "pointer",
          }}
        >
          Reset
        </button>

        {onLoadDesk && onLoadSavedDesk && (
          <LoadDeskMenu onLoadDesk={onLoadDesk} onLoadSavedDesk={onLoadSavedDesk} />
        )}

        <SnapshotMenu teams={teams} sessions={sessions} onLoadSnapshot={onLoadSnapshot} />

        <SettingsMenu
          bellSound={bellSound}
          onBellSoundChange={onBellSoundChange}
          scene={scene}
          onSceneChange={onSceneChange}
          showManager={showManager}
          onShowManagerChange={onShowManagerChange}
          managerPatrolIntervalSec={managerPatrolIntervalSec}
          managerIdleGraceSec={managerIdleGraceSec}
          onManagerPatrolIntervalChange={onManagerPatrolIntervalChange}
          onManagerIdleGraceChange={onManagerIdleGraceChange}
          codeTheme={codeTheme}
          onCodeThemeChange={onCodeThemeChange}
          dockerPersist={dockerPersist}
          onDockerPersistChange={onDockerPersistChange}
          verbose={verbose}
          onVerboseChange={onVerboseChange}
        />
      </div>

      <style>{`@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.4} }`}</style>
    </div>
  );
}
