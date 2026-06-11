import { createContext, useContext } from "react";

export const TEAM_ROW_HEIGHT = 440;

export type TeamRowPanelContextValue = {
  root: HTMLElement | null;
  height: number;
};

export const TeamRowPanelContext = createContext<TeamRowPanelContextValue>({
  root: null,
  height: TEAM_ROW_HEIGHT,
});

export function useTeamRowPanel() {
  return useContext(TeamRowPanelContext);
}
