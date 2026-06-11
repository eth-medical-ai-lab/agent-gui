import { describe, expect, it } from "vitest";
import { buildDeskConfigView, pendingStartParams, resolveDeskProfileVisual, DEFAULT_PROFILE_LABEL } from "./deskConfig";
import type { AgentProfile, Session, Team } from "./types";

const global = { base_url: "http://127.0.0.1:11434/v1", model: "qwen3.5:9b" };
const presets = {
  chat: [] as string[],
  lean: ["file", "terminal", "search", "todo", "skills", "memory", "vision", "clarify", "delegation", "code_execution"],
  full: ["file", "terminal", "search", "browser"],
};

const cloudAgent: AgentProfile = {
  id: "cloud", name: "Google", tagline: "", color: "#a78bfa",
  available: true, model: "gemini-3.1-flash-lite",
  base_url: "https://generativelanguage.googleapis.com/v1beta", profile_path: "",
};

describe("pendingStartParams", () => {
  it("omits tools only for default desk on global lean", () => {
    const out = pendingStartParams(
      "desk-1",
      {},
      { "desk-1": { agentId: "", model: global.model, toolPreset: "lean", toolsEnabled: presets.lean } },
      global,
      presets,
      "lean",
      true,
    );
    expect(out.tools).toBeUndefined();
    expect(out.agent).toBeUndefined();
  });

  it("sends tools when an agent profile is assigned (even on lean)", () => {
    const filteredLean = ["file", "terminal", "search"];
    const out = pendingStartParams(
      "desk-1",
      {
        "desk-1": {
          agentId: "local-researcher",
          agentName: "Local Researcher",
          agentColor: "#e67e22",
          toolPreset: "lean",
          toolsEnabled: filteredLean,
        },
      },
      {},
      global,
      presets,
      "lean",
      true,
    );
    expect(out.agent).toBe("local-researcher");
    expect(out.tools).toEqual(filteredLean);
  });

  it("sends tools when lean selection differs from global lean", () => {
    const customLean = ["file", "terminal"];
    const out = pendingStartParams(
      "desk-1",
      {},
      { "desk-1": { agentId: "", model: global.model, toolPreset: "lean", toolsEnabled: customLean } },
      global,
      presets,
      "lean",
      true,
    );
    expect(out.tools).toEqual(customLean);
  });

  it("does not send the global model as an override for a profile desk", () => {
    // No per-desk override → the profile's own config.yaml owns the model, so we
    // must NOT pass the global/manager model (it would override the profile).
    const out = pendingStartParams(
      "desk-1",
      {
        "desk-1": {
          agentId: "cloud",
          agentName: "Google",
          agentColor: "#a78bfa",
          toolPreset: "lean",
          toolsEnabled: presets.lean,
        },
      },
      {},
      global,
      presets,
      "lean",
      true,
    );
    expect(out.agent).toBe("cloud");
    expect(out.model).toBeUndefined();
  });

  it("sends a per-desk model override for a profile desk when set", () => {
    const out = pendingStartParams(
      "desk-1",
      {
        "desk-1": {
          agentId: "cloud",
          agentName: "Google",
          agentColor: "#a78bfa",
          toolPreset: "lean",
          toolsEnabled: presets.lean,
          modelOverride: "gemini-3.1-pro",
        },
      },
      {},
      global,
      presets,
      "lean",
      true,
    );
    expect(out.model).toBe("gemini-3.1-pro");
  });

  it("sends empty tools for profile chat preset", () => {
    const out = pendingStartParams(
      "desk-1",
      {
        "desk-1": {
          agentId: "researcher",
          agentName: "Researcher",
          agentColor: "#e67e22",
          toolPreset: "chat",
          toolsEnabled: [],
        },
      },
      {},
      global,
      presets,
      "lean",
      true,
    );
    expect(out.tools).toEqual([]);
  });
});

describe("buildDeskConfigView model precedence", () => {
  const tools = (id: string, desks: Team["desks"]): Team[] => [
    { id, color: "blue", desks } as Team,
  ];

  it("a pending desk with a profile shows the profile's model, not global", () => {
    const teams = tools("t1", [{ id: "d1", isPending: true }]);
    const view = buildDeskConfigView(
      "d1", teams, [cloudAgent],
      { d1: { agentId: "cloud", agentName: "Google", agentColor: "#a78bfa", toolPreset: "lean", toolsEnabled: [] } },
      {}, global, presets, "lean",
    );
    expect(view?.model).toBe("gemini-3.1-flash-lite");
    expect(view?.model).not.toBe(global.model);
  });

  it("a session desk with a profile but no runtime model shows the profile's model", () => {
    const session: Session = {
      id: "d2", started_at: "", ended_at: null, source: "", model: "",
      parent_session_id: null, title: "", message_count: 0, token_estimate: 0,
      agent: "cloud", agent_model: "",
    } as Session;
    const teams = tools("t1", [session]);
    const view = buildDeskConfigView(
      "d2", teams, [cloudAgent], {}, {}, global, presets, "lean",
    );
    expect(view?.model).toBe("gemini-3.1-flash-lite");
  });

  it("an explicit per-desk model override still wins", () => {
    const teams = tools("t1", [{ id: "d3", isPending: true }]);
    const view = buildDeskConfigView(
      "d3", teams, [cloudAgent],
      { d3: { agentId: "cloud", agentName: "Google", agentColor: "#a78bfa", toolPreset: "lean", toolsEnabled: [], modelOverride: "gemini-3.1-pro" } },
      {}, global, presets, "lean",
    );
    expect(view?.model).toBe("gemini-3.1-pro");
  });
});

describe("resolveDeskProfileVisual", () => {
  it("returns default label and global model when no agent is assigned", () => {
    const vis = resolveDeskProfileVisual({
      deskCfg: {
        deskId: "d1",
        isPending: true,
        isGlobal: true,
        agentId: "",
        agentProfile: null,
        model: "qwen3.5:9b",
        profileModel: "qwen3.5:9b",
        baseUrl: "http://127.0.0.1:11434/v1",
        toolPreset: "lean",
        toolsEnabled: [],
        benchAssigned: false,
        customized: false,
      },
    });
    expect(vis.isDefault).toBe(true);
    expect(vis.label).toBe(DEFAULT_PROFILE_LABEL);
    expect(vis.model).toBe("qwen3.5:9b");
    expect(vis.agentId).toBe("");
  });

  it("uses named profile when session has agent", () => {
    const vis = resolveDeskProfileVisual({
      session: {
        id: "s1", started_at: "", ended_at: null, source: "", model: "",
        parent_session_id: null, title: "", message_count: 0, token_estimate: 0,
        agent: "coder", agent_model: "gpt-4",
      },
      agents: [{
        id: "coder", name: "Coder", tagline: "", color: "#4a8eff",
        available: true, model: "gpt-4", base_url: "", profile_path: "",
      }],
    });
    expect(vis.isDefault).toBe(false);
    expect(vis.label).toBe("Coder");
    expect(vis.model).toBe("gpt-4");
  });

  it("uses avatar prefs for desk color and archetype", () => {
    const vis = resolveDeskProfileVisual({
      pendingAssignment: {
        agentId: "coder",
        agentName: "Coder",
        agentColor: "#4a8eff",
        toolPreset: "lean",
        toolsEnabled: [],
      },
      agents: [{
        id: "coder", name: "Coder", tagline: "", color: "#4a8eff",
        available: true, model: "gpt-4", base_url: "", profile_path: "",
      }],
      getAvatarPref: () => ({ archetype: "researcher", color: "#ff6b9d" }),
    });
    expect(vis.color).toBe("#ff6b9d");
    expect(vis.archetype).toBe("researcher");
  });
});
