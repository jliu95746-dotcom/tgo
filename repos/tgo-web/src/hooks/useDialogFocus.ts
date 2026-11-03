import { useEffect, useRef } from 'react';

const activeDialogs: HTMLElement[] = [];
const focusableSelector = 'button, input:not([type="hidden"]), select, textarea, a[href], summary, [tabindex]';
const isFocusable = (node: HTMLElement) => node.tabIndex >= 0 && !node.matches(':disabled')
  && !node.closest('[inert], [aria-hidden="true"]') && node.getClientRects().length > 0
  && window.getComputedStyle(node).visibility !== 'hidden';

// DOM order also covers children whose React effects run before the parent.
function topDialog() {
  return activeDialogs.filter(dialog => dialog.isConnected).reduce<HTMLElement | undefined>((top, dialog) =>
    !top || top.contains(dialog) || (!dialog.contains(top) && Boolean(top.compareDocumentPosition(dialog) & Node.DOCUMENT_POSITION_FOLLOWING))
      ? dialog : top, undefined);
}

/** Keep keyboard focus inside the dialog and restore its opener on close. */
export function useDialogFocus<T extends HTMLElement>(onClose: () => void, enabled = true) {
  const element = useRef<T>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!enabled || !element.current) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = element.current;
    const originalTabIndex = dialog.getAttribute('tabindex');
    if (originalTabIndex === null) dialog.setAttribute('tabindex', '-1');
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(isFocusable);
    const focusInside = () => {
      const items = focusable();
      (items.find(item => item.hasAttribute('data-dialog-initial-focus')) ?? items[0] ?? dialog).focus({ preventScroll: true });
    };
    activeDialogs.push(dialog);
    if (topDialog() === dialog) focusInside();
    const handleKey = (event: KeyboardEvent) => {
      if (topDialog() !== dialog || event.defaultPrevented) return;
      if (event.key === 'Escape') { event.preventDefault(); close.current(); }
      if (event.key !== 'Tab') return;
      const items = focusable();
      const first = items[0];
      const last = items[items.length - 1];
      if (!first) { event.preventDefault(); dialog.focus(); return; }
      if (!dialog.contains(document.activeElement) || document.activeElement === dialog) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    const handleFocus = (event: FocusEvent) => {
      if (topDialog() === dialog && !(event.target instanceof HTMLElement && dialog.contains(event.target))) focusInside();
    };
    document.addEventListener('keydown', handleKey);
    document.addEventListener('focusin', handleFocus);
    return () => {
      const wasTop = topDialog() === dialog || (!dialog.isConnected && !topDialog()?.contains(document.activeElement));
      const index = activeDialogs.indexOf(dialog);
      if (index >= 0) activeDialogs.splice(index, 1);
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('focusin', handleFocus);
      if (originalTabIndex === null) dialog.removeAttribute('tabindex');
      const remaining = topDialog();
      if (!wasTop) return;
      if (opener?.isConnected && isFocusable(opener) && (!remaining || remaining.contains(opener))) opener.focus({ preventScroll: true });
      else if (remaining && !remaining.contains(document.activeElement)) {
        (Array.from(remaining.querySelectorAll<HTMLElement>(focusableSelector)).find(isFocusable) ?? remaining).focus({ preventScroll: true });
      }
    };
  }, [enabled]);
  return element;
}
