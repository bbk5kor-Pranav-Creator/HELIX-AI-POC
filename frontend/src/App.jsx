import { Navigate, Route, Routes } from 'react-router-dom';
import Login from './pages/Login';
import AdminDashboard from './pages/AdminDashboard';
import EmployeeDashboard from './pages/EmployeeDashboard';
import ProcessUnderstanding from './pages/ProcessUnderstanding';
import KnowledgeTransfer from './pages/KnowledgeTransfer';
import AIInterviewStudio from './pages/AIInterviewStudio';
import EmployeeDashboardAnalytics from './pages/EmployeeDashboardAnalytics';

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<Login />} />
      <Route path="/admin" element={<AdminDashboard />} />
      <Route path="/employee" element={<EmployeeDashboard />} />
      <Route path="/employee/studio/process-understanding" element={<ProcessUnderstanding />} />
      <Route path="/employee/studio/knowledge-transfer"element={<KnowledgeTransfer />} />
      <Route path="/employee/studio/ai-interview" element={<AIInterviewStudio />}/>
      <Route path="/employee/dashboard" element={<EmployeeDashboardAnalytics />}/>
    </Routes>
  );
}

export default App;