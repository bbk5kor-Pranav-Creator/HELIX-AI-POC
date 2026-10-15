import { Navigate, Outlet, Route, Routes } from 'react-router-dom';
import Login from './pages/Login';
import AdminDashboard from './pages/AdminDashboard';
import EmployeeDashboard from './pages/EmployeeDashboard';
import ProcessUnderstanding from './pages/ProcessUnderstanding';
import KnowledgeTransfer from './pages/KnowledgeTransfer';
import AIInterviewStudio from './pages/AIInterviewStudio';
import EmployeeDashboardAnalytics from './pages/EmployeeDashboardAnalytics';
import { getEmployeeSession } from './utils/auth';

function EmployeeRoutes() {
  return getEmployeeSession() ? <Outlet /> : <Navigate to="/login" replace />;
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<Login />} />
      <Route path="/admin" element={<AdminDashboard />} />
      <Route path="/employee" element={<EmployeeRoutes />}>
        <Route index element={<EmployeeDashboard />} />
        <Route
          path="studio/process-understanding"
          element={<ProcessUnderstanding />}
        />
        <Route path="studio/knowledge-transfer" element={<KnowledgeTransfer />} />
        <Route path="studio/ai-interview" element={<AIInterviewStudio />} />
        <Route path="dashboard" element={<EmployeeDashboardAnalytics />} />
      </Route>
    </Routes>
  );
}

export default App;