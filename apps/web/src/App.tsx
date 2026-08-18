import { BrowserRouter, Route, Routes } from "react-router-dom";
import Landing from "./pages/Landing";
import WizardPage from "./pages/WizardPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<WizardPage />} />
      </Routes>
    </BrowserRouter>
  );
}
