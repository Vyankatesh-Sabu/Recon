import { NavLink, Outlet } from "react-router-dom"

const links = [
  { to: "/", label: "Run console", end: true },
  { to: "/exceptions", label: "Exceptions" },
  { to: "/clearing", label: "Clearing control" },
  { to: "/chain", label: "Chain explorer" },
  { to: "/qa", label: "Ask" },
]

/** The page shell: ink ground, hairline rule under the nav, Inter labels.
 * UI_SPEC.md §1's motion rule applies here too — no hover lifts, this is
 * chrome, not a screen worth animating. */
export function Layout() {
  return (
    <div className="min-h-screen bg-ink text-figure">
      <header className="border-b border-rule">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-8">
          <span className="font-semibold tracking-tight text-sm">RECON-4</span>
          <nav className="flex gap-6 text-sm">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) =>
                  isActive ? "text-figure" : "text-muted hover:text-figure transition-colors"
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
