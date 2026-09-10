import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import './index.css'
import App from './App'
import { SeasonProvider } from './state'
import TeamsPage from './pages/TeamsPage'
import TeamPage from './pages/TeamPage'
import PlayerPage from './pages/PlayerPage'
import ComparePage from './pages/ComparePage'
import DefenseLabPage from './pages/DefenseLabPage'
import CoachPage from './pages/CoachPage'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <TeamsPage /> },
      { path: 'team/:abbr', element: <TeamPage /> },
      { path: 'player/:id', element: <PlayerPage /> },
      { path: 'compare', element: <ComparePage /> },
      { path: 'defense', element: <DefenseLabPage /> },
      { path: 'coach/:abbr', element: <CoachPage /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SeasonProvider>
      <RouterProvider router={router} />
    </SeasonProvider>
  </StrictMode>,
)
