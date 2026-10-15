const EMPLOYEE_SESSION_KEY = 'helix.employeeSession';

export function saveEmployeeSession(session) {
  sessionStorage.setItem(EMPLOYEE_SESSION_KEY, JSON.stringify(session));
}

export function getEmployeeSession() {
  const storedSession = sessionStorage.getItem(EMPLOYEE_SESSION_KEY);

  if (!storedSession) {
    return null;
  }

  try {
    const session = JSON.parse(storedSession);
    return session.role === 'Employee' && session.email ? session : null;
  } catch {
    return null;
  }
}

export function clearEmployeeSession() {
  sessionStorage.removeItem(EMPLOYEE_SESSION_KEY);
}
