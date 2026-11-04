import { useEffect, type RefObject } from 'react';

/** The embedded browser's visible height changes when its keyboard opens. */
export function useChatViewport(element: RefObject<HTMLDivElement | null>, isChatRoute: boolean) {
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    const root = document.documentElement;
    const attribute = isChatRoute ? 'data-chat-workspace' : 'data-management-workspace';
    const previous = root.getAttribute(attribute);
    const previousHeight = root.style.getPropertyValue('--workspace-visible-height');
    root.setAttribute(attribute, 'true');
    let frame = 0;
    const update = () => {
      const viewport = window.visualViewport;
      const height = viewport && viewport.scale === 1 ? viewport.height : window.innerHeight;
      node.style.setProperty('--chat-workspace-height', `${height}px`);
      root.style.setProperty('--workspace-visible-height', `${height}px`);
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('resize', schedule);
      node.style.removeProperty('--chat-workspace-height');
      if (previousHeight) root.style.setProperty('--workspace-visible-height', previousHeight);
      else root.style.removeProperty('--workspace-visible-height');
      if (previous === null) root.removeAttribute(attribute);
      else root.setAttribute(attribute, previous);
    };
  }, [element, isChatRoute]);
}
