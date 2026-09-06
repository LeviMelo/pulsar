/* What a screen shows when it throws.
 *
 * The old console caught render errors and printed the message into a banner,
 * which was the one thing it got right about failure: a blank page tells the
 * operator nothing, and this is a local tool whose reader can act on a stack
 * trace. The router's boundary keeps the failure inside the content area, so
 * the navigation and the header still work.
 */

import { useRouteError } from 'react-router-dom';
import { Banner } from './ui';

export function ViewError() {
  const error = useRouteError() as Error | undefined;
  return (
    <Banner tone="bad">
      <div>
        <b>This view failed to render. </b>
        {String(error?.message || error || 'Unknown error')}
        {error?.stack && (
          <pre className="pre" style={{ marginTop: '10px', maxHeight: '260px' }}>
            {error.stack}
          </pre>
        )}
      </div>
    </Banner>
  );
}
