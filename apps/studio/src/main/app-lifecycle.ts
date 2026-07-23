export interface LifecycleStoppable {
  stop(): Promise<void>;
}

export interface PreventableQuitEvent {
  preventDefault(): void;
}

export class ApplicationLifecycleCoordinator {
  #forge: LifecycleStoppable | null = null;
  #codex: LifecycleStoppable | null = null;
  #unregisterIpc: (() => void) | null = null;
  #closePromise: Promise<readonly Error[]> | null = null;
  #closing = false;

  public ownForge(forge: LifecycleStoppable): void {
    this.#assertOpen();
    this.#forge = forge;
  }

  public ownCodex(codex: LifecycleStoppable): void {
    this.#assertOpen();
    this.#codex = codex;
  }

  public ownIpc(unregisterIpc: () => void): void {
    this.#assertOpen();
    this.#unregisterIpc = unregisterIpc;
  }

  public close(): Promise<readonly Error[]> {
    if (this.#closePromise) {
      return this.#closePromise;
    }
    this.#closing = true;
    const closing = this.#closeOwned();
    this.#closePromise = closing;
    return closing;
  }

  async #closeOwned(): Promise<readonly Error[]> {
    const failures: Error[] = [];
    const attempt = async (operation: () => void | Promise<void>): Promise<void> => {
      try {
        await operation();
      } catch (error) {
        failures.push(toError(error));
      }
    };

    const unregisterIpc = this.#unregisterIpc;
    this.#unregisterIpc = null;
    if (unregisterIpc) {
      await attempt(unregisterIpc);
    }

    const codex = this.#codex;
    this.#codex = null;
    if (codex) {
      await attempt(async () => codex.stop());
    }

    const forge = this.#forge;
    this.#forge = null;
    if (forge) {
      await attempt(async () => forge.stop());
    }
    return failures;
  }

  #assertOpen(): void {
    if (this.#closing) {
      throw new Error("Application shutdown has already started");
    }
  }
}

export class ApplicationQuitGate {
  #shutdownStarted = false;
  #allowQuit = false;

  public constructor(
    private readonly lifecycle: ApplicationLifecycleCoordinator,
    private readonly requestQuit: () => void,
  ) {}

  public handle(event: PreventableQuitEvent): void {
    if (this.#allowQuit) {
      return;
    }
    event.preventDefault();
    if (this.#shutdownStarted) {
      return;
    }
    this.#shutdownStarted = true;
    void this.lifecycle.close().finally(() => {
      this.#allowQuit = true;
      this.requestQuit();
    });
  }
}

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error("Application resource shutdown failed");
}
