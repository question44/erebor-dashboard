import { Component, Input, inject, signal } from '@angular/core';
import { AbstractControl, FormBuilder, ReactiveFormsModule, ValidationErrors, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatStepperModule } from '@angular/material/stepper';
import { Icon } from './icon';
import { Session } from './session';

export const USERNAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,31}$/;
export const passwordsMatch = (group: AbstractControl): ValidationErrors | null =>
  group.get('password')?.value === group.get('confirm')?.value ? null : { mismatch: true };

/** First-run wizard: shown only while the server has no accounts. */
@Component({
  selector: 'erebor-setup',
  imports: [ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatInputModule, MatStepperModule, Icon],
  template: `
<main class="auth-page">
  <div class="auth-card wide">
    <div class="auth-mark"><erebor-icon name="shield" /></div>
    <h1>Set up {{ session.branding.title }}</h1>
    <p class="intro">No accounts exist yet. Create the administrator who will manage users and decide what each one can see.</p>
    <mat-stepper linear orientation="vertical" #stepper>
      <mat-step label="Welcome" [completed]="true">
        <p>The first account becomes the <strong>administrator</strong>. Administrators always see every module, service and action, and can add other users from <em>Manage users</em> in the account menu.</p>
        <p class="hint">This page is only available until the first account is created. Finish it now from a device you trust.</p>
        <div class="step-actions"><button mat-flat-button matStepperNext>Get started</button></div>
      </mat-step>
      <mat-step label="Administrator account" [stepControl]="form">
        <form [formGroup]="form" class="auth-form" (ngSubmit)="stepper.next()">
          <mat-form-field appearance="outline">
            <mat-label>Username</mat-label>
            <input matInput formControlName="username" autocomplete="username" autocapitalize="none" spellcheck="false" required>
            <mat-hint>Letters, digits, dots, dashes or underscores. Stored in lowercase.</mat-hint>
            @if (form.controls.username.hasError('pattern')) { <mat-error>Use up to 32 letters, digits, dots, dashes or underscores.</mat-error> }
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Password</mat-label>
            <input matInput type="password" formControlName="password" autocomplete="new-password" required>
            <mat-hint>At least 8 characters.</mat-hint>
            @if (form.controls.password.hasError('minlength')) { <mat-error>Use at least 8 characters.</mat-error> }
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Confirm password</mat-label>
            <input matInput type="password" formControlName="confirm" autocomplete="new-password" required>
          </mat-form-field>
          @if (form.hasError('mismatch') && form.controls.confirm.touched) { <p class="form-error" role="alert">Passwords do not match.</p> }
          <div class="step-actions">
            <button mat-button type="button" matStepperPrevious>Back</button>
            <button mat-flat-button type="submit" [disabled]="form.invalid">Continue</button>
          </div>
        </form>
      </mat-step>
      <mat-step label="Create">
        <p>Create administrator <strong>{{ normalized() }}</strong> and sign in?</p>
        <p class="hint">Next, open <em>Manage users</em> to add accounts and tick the modules, services and actions each person may use.</p>
        @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
        <div class="step-actions">
          <button mat-button matStepperPrevious [disabled]="busy()">Back</button>
          <button mat-flat-button (click)="create()" [disabled]="busy() || form.invalid">{{ busy() ? 'Creating…' : 'Create administrator' }}</button>
        </div>
      </mat-step>
    </mat-stepper>
  </div>
</main>`,
})
export class Setup {
  readonly session = inject(Session);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['admin', [Validators.required, Validators.pattern(USERNAME_PATTERN)]],
    password: ['', [Validators.required, Validators.minLength(8), Validators.maxLength(256)]],
    confirm: ['', Validators.required],
  }, { validators: passwordsMatch });

  normalized() { return this.form.controls.username.value.trim().toLowerCase(); }

  async create() {
    if (this.form.invalid || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.session.setup(this.normalized(), this.form.controls.password.value);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : 'Setup failed');
      // Someone else finished setup first: go to sign-in instead.
      if (error instanceof Error && 'status' in error && error.status === 409) void this.session.check();
    } finally {
      this.busy.set(false);
    }
  }
}

@Component({
  selector: 'erebor-login',
  imports: [ReactiveFormsModule, MatButtonModule, MatFormFieldModule, MatInputModule, Icon],
  template: `
<main class="auth-page">
  <form class="auth-card auth-form" [formGroup]="form" (ngSubmit)="submit()">
    <div class="auth-mark"><erebor-icon name="user" /></div>
    <h1>{{ session.branding.title }}</h1>
    <p class="intro">Sign in to see your services and controls.</p>
    @if (message && !error()) { <p class="notice" role="status">{{ message }}</p> }
    <mat-form-field appearance="outline">
      <mat-label>Username</mat-label>
      <input matInput formControlName="username" autocomplete="username" autocapitalize="none" spellcheck="false" required>
    </mat-form-field>
    <mat-form-field appearance="outline">
      <mat-label>Password</mat-label>
      <input matInput type="password" formControlName="password" autocomplete="current-password" required>
    </mat-form-field>
    @if (error()) { <p class="form-error" role="alert">{{ error() }}</p> }
    <button mat-flat-button type="submit" [disabled]="form.invalid || busy()">{{ busy() ? 'Signing in…' : 'Sign in' }}</button>
  </form>
</main>`,
})
export class Login {
  @Input() message?: string;
  readonly session = inject(Session);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = inject(FormBuilder).nonNullable.group({
    username: ['', Validators.required],
    password: ['', Validators.required],
  });

  async submit() {
    if (this.form.invalid || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.session.login(this.form.controls.username.value.trim().toLowerCase(), this.form.controls.password.value);
    } catch (error) {
      this.error.set(error instanceof TypeError ? 'Could not reach the dashboard server.'
        : error instanceof Error ? error.message : 'Sign-in failed');
      this.form.controls.password.reset();
    } finally {
      this.busy.set(false);
    }
  }
}
