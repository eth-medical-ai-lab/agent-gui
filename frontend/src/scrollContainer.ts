/** Scroll `el` within `container` only — does not move outer floor/strip scrollers. */
export function scrollIntoContainer(
  container: HTMLElement,
  el: HTMLElement,
  block: "start" | "center" | "end" | "nearest" = "end",
): void {
  const cRect = container.getBoundingClientRect();
  const eRect = el.getBoundingClientRect();

  if (block === "end") {
    if (eRect.bottom > cRect.bottom) container.scrollTop += eRect.bottom - cRect.bottom;
    else if (eRect.top < cRect.top) container.scrollTop += eRect.top - cRect.top;
    return;
  }
  if (block === "start") {
    if (eRect.top < cRect.top) container.scrollTop += eRect.top - cRect.top;
    else if (eRect.bottom > cRect.bottom) container.scrollTop += eRect.bottom - cRect.bottom;
    return;
  }
  if (block === "center") {
    container.scrollTop += eRect.top - cRect.top - container.clientHeight / 2 + eRect.height / 2;
    return;
  }
  // nearest
  if (eRect.bottom > cRect.bottom) container.scrollTop += eRect.bottom - cRect.bottom;
  else if (eRect.top < cRect.top) container.scrollTop += eRect.top - cRect.top;
}

export function scrollContainerToBottom(container: HTMLElement): void {
  container.scrollTop = container.scrollHeight;
}
