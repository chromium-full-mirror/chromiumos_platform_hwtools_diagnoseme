// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Routes} from '@angular/router';

/** A single entry of the side menu of an edition. */
export interface EditionMenuItem {
  /** Human readable label shown in the menu. */
  label?: string;
  /** Absolute router link of the page. */
  route?: string;
  /** When true, a divider is rendered instead of a button. */
  divider?: boolean;
}

/**
 * A DiagnoseMe edition (product flavor).
 *
 * Every shipped DiagnoseMe build contains exactly one edition. The active
 * edition is selected at build time via Angular `fileReplacements` of
 * `active-edition.ts` (see angular.json configurations and editions.json in
 * the repository root).
 */
export interface Edition {
  /** Stable identifier, must match editions.json and the server registry. */
  id: string;
  /** Title shown in the toolbar. */
  title: string;
  /** Route to redirect to on an empty or unknown URL. */
  landing: string;
  /** Side menu entries. */
  menu: EditionMenuItem[];
  /** Lazily loaded routes of this edition. */
  routes: Routes;
}
