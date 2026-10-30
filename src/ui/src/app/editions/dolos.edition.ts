// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {DOLOS_ROUTES, DOLOS_MENU} from './edition-routes';

/** DiagnoseMe edition: Dolos. */
export const EDITION: Edition = {
  id: 'dolos',
  title: 'Dolos',
  landing: '/dolos-rework',
  menu: DOLOS_MENU,
  routes: DOLOS_ROUTES,
};
