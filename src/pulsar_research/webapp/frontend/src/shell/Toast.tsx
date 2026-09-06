/* Transient confirmation, mounted once in the shell.
 *
 * Registered as the module-level toaster so non-component code — the clipboard
 * helper inside a record, a mutation's error path — can raise one without every
 * caller threading a hook down through five layers.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { registerToaster } from '../components/records';
import type { Tone } from '../components/ui';

export function Toast() {
  const [message, setMessage] = useState<{ text: string; tone: Tone } | null>(null);
  const timer = useRef<number | null>(null);

  const show = useCallback((text: string, tone: Tone = '') => {
    setMessage({ text, tone });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(null), tone === 'bad' ? 6000 : 2600);
  }, []);

  useEffect(() => {
    registerToaster(show);
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [show]);

  if (!message) return null;
  return (
    <div className={`toast${message.tone ? ` ${message.tone}` : ''}`}>{message.text}</div>
  );
}
