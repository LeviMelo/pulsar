import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createHashRouter, Navigate, RouterProvider } from 'react-router-dom';

import { Shell } from './shell/Shell';
import { Overview } from './views/Overview';
import { Opportunities } from './views/Opportunities';
import { Professors } from './views/Professors';
import { Landscape } from './views/Landscape';
import { Network } from './views/Network';
import { Campaigns } from './views/Campaigns';
import { Engine } from './views/Engine';
import { ViewError } from './components/ViewError';
import './styles/app.css';

/* Hash routing on purpose: the console is served by a twenty-line static
 * handler inside the Python process, and history routing would require it to
 * understand which paths are the app's and which are files. A hash keeps every
 * request pointed at index.html without the server knowing the route table.
 */
const router = createHashRouter([
  {
    path: '/',
    element: <Shell />,
    errorElement: <ViewError />,
    children: [
      { index: true, element: <Navigate to="/overview" replace /> },
      { path: 'overview', element: <Overview />, errorElement: <ViewError /> },
      { path: 'opportunities', element: <Opportunities />, errorElement: <ViewError /> },
      { path: 'professors', element: <Professors />, errorElement: <ViewError /> },
      { path: 'landscape', element: <Landscape />, errorElement: <ViewError /> },
      { path: 'network', element: <Network />, errorElement: <ViewError /> },
      { path: 'campaigns', element: <Campaigns />, errorElement: <ViewError /> },
      { path: 'engine', element: <Engine />, errorElement: <ViewError /> },
      { path: '*', element: <Navigate to="/overview" replace /> },
    ],
  },
]);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
