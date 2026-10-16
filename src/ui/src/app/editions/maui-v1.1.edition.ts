// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {MAUI_V11_ROUTES, MAUI_V11_MENU} from './edition-routes';

/** DiagnoseMe edition: Maui v1.1. */
export const EDITION: Edition = {
  id: 'maui-v1.1',
  title: 'Maui v1.1',
  landing: '/maui/v1.1/provisioning',
  menu: MAUI_V11_MENU,
  routes: MAUI_V11_ROUTES,
};
