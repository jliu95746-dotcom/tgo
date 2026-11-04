import { useEffect, useState } from 'react';

/** Keep JS-only controls aligned with the existing 900px chat breakpoint. */
export function useCompactChatLayout() {
  const [compact, setCompact] = useState(() => window.matchMedia('(max-width: 899px)').matches);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 899px)');
    const update = () => setCompact(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return compact;
}
