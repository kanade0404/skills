export { default, Control, Reservations, IsolatedCoordinator } from './authority.mjs';
import { IsolatedCoordinator } from './authority.mjs';
// Local-only fixture. Never included in deployable entrypoints.
export class DeadlineFixture extends IsolatedCoordinator {
  armDeadline() {
    const until=Date.now()+200;
    this.env.TEST_UNTIL=new Date(until).toISOString();
    return {until};
  }
}
