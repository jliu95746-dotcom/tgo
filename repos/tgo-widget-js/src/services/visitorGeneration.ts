let revision = 0

export function invalidateVisitorGeneration(): number { return ++revision }
export function visitorGeneration(): number { return revision }

export function captureVisitorGeneration() {
  const captured = revision
  return {
    isCurrent: () => captured === revision,
    assertCurrent: () => { if (captured !== revision) throw new Error('当前客户会话已变化，请重新发送。') },
  }
}

export function guardVisitorAction<State>(
  write: (patch: Partial<State> | ((state: State) => Partial<State>)) => void,
  read: () => State,
) {
  const scope = captureVisitorGeneration()
  return {
    ...scope,
    set: (patch: Partial<State> | ((state: State) => Partial<State>)) => { if (scope.isCurrent()) write(patch) },
    get: (): State => { scope.assertCurrent(); return read() },
  }
}
