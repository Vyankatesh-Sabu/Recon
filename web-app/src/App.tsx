import { Route, Routes } from "react-router-dom"
import { Layout } from "./components/Layout"
import { AskConsole } from "./routes/AskConsole"
import { ChainExplorer } from "./routes/ChainExplorer"
import { ClearingControl } from "./routes/ClearingControl"
import { Exceptions } from "./routes/Exceptions"
import { RunConsole } from "./routes/RunConsole"

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<RunConsole />} />
        <Route path="exceptions" element={<Exceptions />} />
        <Route path="clearing" element={<ClearingControl />} />
        <Route path="chain" element={<ChainExplorer />} />
        <Route path="qa" element={<AskConsole />} />
      </Route>
    </Routes>
  )
}
