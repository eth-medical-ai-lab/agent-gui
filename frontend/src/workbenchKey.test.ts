import { describe, it, expect } from "vitest";
import { WORKBENCH_KEY_V2, workbenchStorageKey, getWorkbenchKey, setActiveOfficeId, isOfficeResolved, resetActiveOffice } from "./workbenchKey";

describe("workbenchStorageKey", () => {
  it("keeps the historical key for the default office", () => {
    expect(workbenchStorageKey("")).toBe(WORKBENCH_KEY_V2);
    expect(workbenchStorageKey(null)).toBe(WORKBENCH_KEY_V2);
    expect(workbenchStorageKey(undefined)).toBe(WORKBENCH_KEY_V2);
    expect(workbenchStorageKey("   ")).toBe(WORKBENCH_KEY_V2);
  });
  it("suffixes non-default offices", () => {
    expect(workbenchStorageKey("hermes-lab")).toBe("hermes-workbench-v2:hermes-lab");
  });
  it("sanitizes odd characters", () => {
    expect(workbenchStorageKey("a b/c")).toBe("hermes-workbench-v2:a-b-c");
  });
});

describe("active office singleton", () => {
  it("falls back to the bare key until resolved, then follows the office id", () => {
    resetActiveOffice();
    expect(isOfficeResolved()).toBe(false);
    expect(getWorkbenchKey()).toBe(WORKBENCH_KEY_V2);
    setActiveOfficeId("hermes-lab");
    expect(isOfficeResolved()).toBe(true);
    expect(getWorkbenchKey()).toBe("hermes-workbench-v2:hermes-lab");
    setActiveOfficeId("");
    expect(isOfficeResolved()).toBe(true);
    expect(getWorkbenchKey()).toBe(WORKBENCH_KEY_V2);
    resetActiveOffice();
  });
});
