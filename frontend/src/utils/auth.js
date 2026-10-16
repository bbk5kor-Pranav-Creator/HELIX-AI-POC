const EMPLOYEE_SESSION_KEY = 'helix.employeeSession';

export function saveWorkspaceSession(session) {
  sessionStorage.setItem(EMPLOYEE_SESSION_KEY, JSON.stringify(session));
}

export function getWorkspaceSession() {
  const storedSession = sessionStorage.getItem(EMPLOYEE_SESSION_KEY);

  if (!storedSession) {
    return null;
  }

  try {
    const session = JSON.parse(storedSession);
    return ['Admin', 'Employee'].includes(session.role) &&
      session.email &&
      session.accessToken
      ? session
      : null;
  } catch {
    return null;
  }
}

export function getEmployeeSession() {
  const session = getWorkspaceSession();
  return session?.role === 'Employee' ? session : null;
}

export function clearWorkspaceSession() {
  sessionStorage.removeItem(EMPLOYEE_SESSION_KEY);
}

export function clearEmployeeSession() {
  if (getEmployeeSession()) {
    clearWorkspaceSession();
  }
}
