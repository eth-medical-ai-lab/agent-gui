import React, { useMemo, useState } from "react";
import type { AgentProfile } from "../types";
import { DEFAULT_PROFILE_COLOR, DEFAULT_PROFILE_LABEL } from "../deskConfig";
import { UNSORTED_SECTION_ID, type RosterLayout, type RosterSection } from "../rosterLayout";
import { ENABLE_PROFILE_CREATE } from "../featureFlags";
import { useAvatarPrefs, type AvatarPref } from "../avatarPrefs";
import { AgentFigure } from "./AgentFigure";

export interface AgentRosterProps {
  agents: AgentProfile[];
  defaultModel?: string;
  dragActive?: boolean;
  dropHighlight?: boolean;
  rosterLayout: RosterLayout;
  sectionDropHoverId?: string | null;
  onAgentDragStart?: (e: React.MouseEvent, agentId: string, color?: string) => void;
  onAgentEdit?: (agent: AgentProfile) => void;
  onDefaultEdit?: () => void;
  onCreateAgent?: () => void;
}

const SLOT_W = 80;
const FIGURE_SCALE = 0.78;

function DefaultRosterSlot({
  defaultModel,
  dropHighlight,
  onDragStart,
  onEdit,
}: {
  defaultModel?: string;
  dropHighlight?: boolean;
  onDragStart?: (e: React.MouseEvent, agentId: string, color?: string) => void;
  onEdit?: () => void;
}) {
  const [hov, setHov] = React.useState(false);
  const draggable = !!onDragStart;

  return (
    <div
      style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        width: SLOT_W, minWidth: SLOT_W, flexShrink: 0,
        transition: "transform 0.15s ease, opacity 0.15s ease",
        position: "relative",
        transform: hov ? "translateY(-2px)" : undefined,
      }}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      title={`${DEFAULT_PROFILE_LABEL} (~/.hermes/config.yaml) — click to edit · drag onto desk`}
    >
      {onEdit && hov && (
        <button
          type="button"
          title="Edit default agent"
          onClick={(e) => { e.stopPropagation(); onEdit(); }}
          style={{
            position: "absolute", top: 0, right: 4, zIndex: 2,
            width: 18, height: 18, borderRadius: 4, fontSize: 10,
            background: "rgba(0,0,0,0.45)", border: "1px solid rgba(255,255,255,0.15)",
            color: "var(--text-dim)", cursor: "pointer", lineHeight: 1,
          }}
        >
          ✎
        </button>
      )}
      <div style={{
        position: "relative", height: 68, width: SLOT_W,
        display: "flex", alignItems: "flex-end", justifyContent: "center",
        borderRadius: 10,
        background: dropHighlight ? "rgba(100,200,255,0.08)" : hov ? "rgba(255,255,255,0.04)" : "transparent",
        border: dropHighlight ? "1px dashed var(--accent2)" : "1px solid transparent",
        transition: "background 0.15s, border-color 0.15s",
      }}>
        <div style={{
          filter: `drop-shadow(0 0 ${hov ? 8 : 4}px ${DEFAULT_PROFILE_COLOR}88)`,
          cursor: draggable ? "grab" : "default",
        }}
          onMouseDown={draggable
            ? (e) => {
                e.preventDefault();
                e.stopPropagation();
                onDragStart!(e, "", DEFAULT_PROFILE_COLOR);
              }
            : undefined}
        >
          <AgentFigure
            color={DEFAULT_PROFILE_COLOR}
            scale={FIGURE_SCALE}
            state="idle"
          />
        </div>
      </div>
      <div
        style={{
          fontSize: 10, fontWeight: 600, color: "var(--text)", marginTop: 5,
          lineHeight: 1.25, textAlign: "center", width: "100%",
          wordBreak: "break-word",
          cursor: onEdit ? "pointer" : "default",
        }}
        onClick={() => { onEdit?.(); }}
      >
        {DEFAULT_PROFILE_LABEL}
      </div>
      {defaultModel && (
        <div style={{
          fontSize: 8, color: "var(--text-dim)", marginTop: 2,
          maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
          title={defaultModel}
        >
          {defaultModel}
        </div>
      )}
    </div>
  );
}

function AgentRosterSlot({
  agent, dropHighlight, onDragStart, onEdit, avatarPref,
}: {
  agent: AgentProfile;
  dropHighlight?: boolean;
  onDragStart?: (e: React.MouseEvent, agentId: string, color?: string) => void;
  onEdit?: (agent: AgentProfile) => void;
  avatarPref?: AvatarPref;
}) {
  const [hov, setHov] = React.useState(false);
  const notInstalled = agent.available === false;
  // A profile may run on several desks at once (each desk is fully isolated), so
  // being in use elsewhere no longer disables it — only a missing install does.
  const inUse = !!agent.inUse;
  const unavailable = notInstalled;
  const draggable = !unavailable && !!onDragStart;
  const displayName = agent.name || agent.id;
  const effectiveColor = avatarPref?.color || agent.color;

  return (
    <div
      style={{
        display: "flex", flexDirection: "column", alignItems: "center",
        width: SLOT_W, minWidth: SLOT_W, flexShrink: 0,
        opacity: unavailable ? 0.35 : 1,
        transition: "transform 0.15s ease, opacity 0.15s ease",
        position: "relative",
        transform: hov && !unavailable ? "translateY(-2px)" : undefined,
      }}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      title={
        notInstalled
          ? `${displayName} — profile not installed`
          : inUse
            ? `${displayName} — running on a desk · click to edit · drag onto another desk or section`
            : `${displayName} — click to edit · drag onto desk or section`
      }
    >
      {onEdit && !unavailable && hov && (
        <button
          type="button"
          title="Edit SOUL + MEMORY"
          onClick={(e) => { e.stopPropagation(); onEdit(agent); }}
          style={{
            position: "absolute", top: 0, right: 4, zIndex: 2,
            width: 18, height: 18, borderRadius: 4, fontSize: 10,
            background: "rgba(0,0,0,0.45)", border: "1px solid rgba(255,255,255,0.15)",
            color: "var(--text-dim)", cursor: "pointer", lineHeight: 1,
          }}
        >
          ✎
        </button>
      )}
      <div style={{
        position: "relative", height: 68, width: SLOT_W,
        display: "flex", alignItems: "flex-end", justifyContent: "center",
        borderRadius: 10,
        background: dropHighlight ? "rgba(100,200,255,0.08)" : hov ? "rgba(255,255,255,0.04)" : "transparent",
        border: dropHighlight ? "1px dashed var(--accent2)" : "1px solid transparent",
        transition: "background 0.15s, border-color 0.15s",
      }}>
        <div style={{
          filter: `drop-shadow(0 0 ${hov ? 8 : 4}px ${effectiveColor}88)`,
          cursor: draggable ? "grab" : "default",
        }}
          onMouseDown={draggable
            ? (e) => { e.preventDefault(); e.stopPropagation(); onDragStart!(e, agent.id, effectiveColor); }
            : undefined}
        >
          <AgentFigure
            agentId={agent.id}
            color={effectiveColor}
            isPrototype={agent.is_prototype}
            cloneFrom={agent.clone_from}
            archetype={avatarPref?.archetype}
            scale={FIGURE_SCALE}
            state="idle"
          />
        </div>
      </div>
      <div
        style={{
          fontSize: 10, fontWeight: 600, color: "var(--text)", marginTop: 5,
          lineHeight: 1.25, textAlign: "center", width: "100%",
          wordBreak: "break-word",
          cursor: onEdit ? "pointer" : "default",
        }}
        onClick={() => { if (onEdit) onEdit(agent); }}
      >
        {displayName}
      </div>
    </div>
  );
}

/**
 * Section header with inline editing of name + blurb. Reused by both the
 * user-configurable sections and the always-present default-agent column.
 */
function EditableHeader({
  name, blurb, color, namePlaceholder = "Section name", onSave, onDelete,
}: {
  name: string;
  blurb: string;
  color: string;
  namePlaceholder?: string;
  onSave?: (patch: { name: string; blurb: string }) => void;
  onDelete?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [hov, setHov] = useState(false);
  const [draftName, setDraftName] = useState(name);
  const [draftBlurb, setDraftBlurb] = useState(blurb);

  function start() { setDraftName(name); setDraftBlurb(blurb); setEditing(true); }
  function save() { onSave?.({ name: draftName.trim() || name, blurb: draftBlurb.trim() }); setEditing(false); }

  if (editing) {
    return (
      <div style={{ marginBottom: 10, display: "flex", flexDirection: "column", gap: 6 }}>
        <input
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          placeholder={namePlaceholder}
          autoFocus
          style={{
            fontSize: 12, fontWeight: 700, color: "var(--text)",
            background: "#0f1626", border: "1px solid #2a3558", borderRadius: 5,
            padding: "4px 7px",
          }}
        />
        <input
          value={draftBlurb}
          onChange={(e) => setDraftBlurb(e.target.value)}
          placeholder="Short description"
          onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
          style={{
            fontSize: 11, color: "var(--text-dim)",
            background: "#0f1626", border: "1px solid #2a3558", borderRadius: 5,
            padding: "4px 7px",
          }}
        />
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" onClick={save} style={miniBtn("#1e5aa8", "#cfe4ff")}>Save</button>
          <button type="button" onClick={() => setEditing(false)} style={miniBtn("transparent", "var(--text-dim)")}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        fontSize: 12, lineHeight: 1.45, marginBottom: 10,
        paddingBottom: 6, borderBottom: "1px solid rgba(255,255,255,0.06)",
        display: "flex", alignItems: "baseline", gap: 6,
      }}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
    >
      <span style={{ fontWeight: 700, color }}>{name}</span>
      {blurb && (
        <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>: {blurb}</span>
      )}
      <span style={{ flex: 1 }} />
      {(onSave || onDelete) && hov && (
        <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          {onSave && (
            <button type="button" title="Edit" onClick={start} style={iconBtn}>✎</button>
          )}
          {onDelete && (
            <button type="button" title="Delete section" onClick={onDelete} style={iconBtn}>🗑</button>
          )}
        </span>
      )}
    </div>
  );
}

/** A user-configurable section: editable name + blurb, deletable, drop target. */
function SectionView({
  section, profiles, highlighted, dropHover, slotProps,
  onUpdate, onRemove,
}: {
  section: RosterSection;
  profiles: AgentProfile[];
  highlighted: boolean;
  dropHover: boolean;
  slotProps: (a: AgentProfile) => {
    agent: AgentProfile;
    dropHighlight: boolean;
    onDragStart?: (e: React.MouseEvent, agentId: string, color?: string) => void;
    onEdit?: (agent: AgentProfile) => void;
    avatarPref?: AvatarPref;
  };
  onUpdate?: (id: string, patch: Partial<Pick<RosterSection, "name" | "blurb">>) => void;
  onRemove?: (id: string) => void;
}) {
  return (
    <section
      data-roster-section={section.id}
      style={{
        marginBottom: 18,
        borderRadius: 8,
        padding: dropHover ? "6px 8px" : 0,
        border: dropHover ? "1px dashed var(--accent2)" : "1px solid transparent",
        background: dropHover ? "rgba(100,200,255,0.06)" : "transparent",
        transition: "background 0.15s, border-color 0.15s, padding 0.1s",
      }}
    >
      <EditableHeader
        name={section.name}
        blurb={section.blurb}
        color={section.color}
        onSave={onUpdate ? (patch) => onUpdate(section.id, patch) : undefined}
        onDelete={onRemove ? () => {
          if (window.confirm(`Delete section "${section.name}"? Its profiles return to their default group.`)) {
            onRemove(section.id);
          }
        } : undefined}
      />

      {profiles.length === 0 ? (
        <div style={{ fontSize: 10, color: "var(--text-dim)", opacity: 0.55, padding: "4px 0 2px" }}>
          {highlighted ? "Drop here to add to this section" : "No profiles yet — drag one here"}
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: 6 }}>
          {profiles.map((a) => (
            <AgentRosterSlot key={a.id} {...slotProps(a)} />
          ))}
        </div>
      )}
    </section>
  );
}

function CreateSectionBox({ onAdd }: { onAdd: (name: string, blurb: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [blurb, setBlurb] = useState("");

  function add() {
    if (!name.trim()) return;
    onAdd(name, blurb);
    setName(""); setBlurb(""); setOpen(false);
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Add a new section"
        style={{
          marginBottom: 14, padding: "6px 10px", fontSize: 11, fontWeight: 600,
          borderRadius: 6, cursor: "pointer",
          border: "1px dashed rgba(255,255,255,0.18)",
          background: "rgba(255,255,255,0.04)", color: "var(--text-dim)",
        }}
      >
        + Create new section
      </button>
    );
  }

  return (
    <div style={{
      marginBottom: 14, display: "flex", flexDirection: "column", gap: 6,
      padding: 10, borderRadius: 8, border: "1px dashed rgba(255,255,255,0.18)",
      background: "rgba(255,255,255,0.03)",
    }}>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Section name"
        autoFocus
        style={{
          fontSize: 12, fontWeight: 700, color: "var(--text)",
          background: "#0f1626", border: "1px solid #2a3558", borderRadius: 5, padding: "4px 7px",
        }}
      />
      <input
        value={blurb}
        onChange={(e) => setBlurb(e.target.value)}
        placeholder="Short description (optional)"
        onKeyDown={(e) => { if (e.key === "Enter") add(); if (e.key === "Escape") setOpen(false); }}
        style={{
          fontSize: 11, color: "var(--text-dim)",
          background: "#0f1626", border: "1px solid #2a3558", borderRadius: 5, padding: "4px 7px",
        }}
      />
      <div style={{ display: "flex", gap: 6 }}>
        <button type="button" onClick={add} style={miniBtn("#1e5aa8", "#cfe4ff")}>Add</button>
        <button type="button" onClick={() => setOpen(false)} style={miniBtn("transparent", "var(--text-dim)")}>Cancel</button>
      </div>
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  width: 18, height: 18, borderRadius: 4, fontSize: 10, lineHeight: 1,
  background: "rgba(0,0,0,0.35)", border: "1px solid rgba(255,255,255,0.15)",
  color: "var(--text-dim)", cursor: "pointer",
};

function miniBtn(bg: string, color: string): React.CSSProperties {
  return {
    padding: "4px 12px", fontSize: 11, fontWeight: 600, borderRadius: 5,
    cursor: "pointer", border: "1px solid rgba(255,255,255,0.15)",
    background: bg, color,
  };
}

export function AgentRoster({
  agents, defaultModel, dragActive, dropHighlight,
  rosterLayout, sectionDropHoverId,
  onAgentDragStart, onAgentEdit, onDefaultEdit, onCreateAgent,
}: AgentRosterProps) {
  const {
    sections, globalSection, updateGlobalSection,
    resolveSectionId, addSection, updateSection, removeSection,
  } = rosterLayout;
  const avatars = useAvatarPrefs();

  const { bySection, unsorted } = useMemo(() => {
    const buckets: Record<string, AgentProfile[]> = {};
    for (const s of sections) buckets[s.id] = [];
    const unsortedList: AgentProfile[] = [];

    for (const agent of agents) {
      const sid = resolveSectionId(agent);
      if (sid && buckets[sid]) buckets[sid].push(agent);
      else unsortedList.push(agent);
    }

    const sortFn = (a: AgentProfile, b: AgentProfile) => {
      if (a.is_prototype && !b.is_prototype) return -1;
      if (!a.is_prototype && b.is_prototype) return 1;
      return (a.name || a.id).localeCompare(b.name || b.id);
    };
    for (const id of Object.keys(buckets)) buckets[id].sort(sortFn);
    unsortedList.sort(sortFn);

    return { bySection: buckets, unsorted: unsortedList };
  }, [agents, sections, resolveSectionId]);

  const highlighted = Boolean(dragActive && dropHighlight);
  const profileCount = agents.length + 1;
  const statusLine = highlighted
    ? "Drop here to stop agent"
    : `${profileCount} profile${profileCount !== 1 ? "s" : ""}`;

  const slotProps = (a: AgentProfile) => ({
    agent: a,
    dropHighlight: highlighted,
    onDragStart: onAgentDragStart,
    onEdit: onAgentEdit,
    avatarPref: avatars.get(a.id),
  });

  return (
    <div>
      <section style={{ marginBottom: 18 }}>
        <EditableHeader
          name={globalSection.name}
          blurb={globalSection.blurb}
          color="var(--text-dim)"
          namePlaceholder="Default column name"
          onSave={updateGlobalSection}
        />
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: 6 }}>
          <DefaultRosterSlot
            defaultModel={defaultModel}
            dropHighlight={highlighted}
            onDragStart={onAgentDragStart}
            onEdit={onDefaultEdit}
          />
        </div>
      </section>

      {sections.map((section) => (
        <SectionView
          key={section.id}
          section={section}
          profiles={bySection[section.id] ?? []}
          highlighted={dragActive === true}
          dropHover={sectionDropHoverId === section.id}
          slotProps={slotProps}
          onUpdate={updateSection}
          onRemove={removeSection}
        />
      ))}

      {unsorted.length > 0 && (
        <section
          data-roster-section={UNSORTED_SECTION_ID}
          style={{
            marginBottom: 18,
            borderRadius: 8,
            padding: sectionDropHoverId === UNSORTED_SECTION_ID ? "6px 8px" : 0,
            border: sectionDropHoverId === UNSORTED_SECTION_ID ? "1px dashed var(--accent2)" : "1px solid transparent",
            background: sectionDropHoverId === UNSORTED_SECTION_ID ? "rgba(100,200,255,0.06)" : "transparent",
            transition: "background 0.15s, border-color 0.15s, padding 0.1s",
          }}
        >
          <div style={{
            fontSize: 12, lineHeight: 1.45, marginBottom: 10,
            paddingBottom: 6, borderBottom: "1px solid rgba(255,255,255,0.06)",
          }}>
            <span style={{ fontWeight: 700, color: "var(--text-dim)" }}>Unsorted</span>
            <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>: Profiles not in a section</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {unsorted.map((a) => (
              <AgentRosterSlot key={a.id} {...slotProps(a)} />
            ))}
          </div>
        </section>
      )}

      <CreateSectionBox onAdd={addSection} />

      {ENABLE_PROFILE_CREATE && onCreateAgent && (
        <button
          type="button"
          onClick={onCreateAgent}
          title="New agent — clone from a template"
          style={{
            display: "block",
            padding: "5px 10px", fontSize: 10, fontWeight: 600,
            borderRadius: 6, cursor: "pointer",
            border: "1px dashed rgba(255,255,255,0.18)",
            background: "rgba(255,255,255,0.04)", color: "var(--text-dim)",
          }}
        >
          + New profile
        </button>
      )}

      <div style={{
        fontSize: 10, color: highlighted ? "var(--accent2)" : "var(--text-dim)",
        opacity: highlighted ? 1 : 0.75,
        marginTop: 12, paddingTop: 8,
        borderTop: "1px solid rgba(255,255,255,0.06)",
      }}>
        {statusLine} · drag onto a desk to assign · drag onto a section to organize
      </div>
    </div>
  );
}
