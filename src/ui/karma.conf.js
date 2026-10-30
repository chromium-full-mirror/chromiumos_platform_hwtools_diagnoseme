// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// Chrome's sandbox does not work as root inside containers/CI. Set
// KARMA_NO_SANDBOX=1 (or run with CI=true) there; local runs keep the sandbox.
const noSandbox = !!(process.env.KARMA_NO_SANDBOX || process.env.CI);

module.exports = function (config) {
  config.set({
    basePath: '',
    frameworks: ['jasmine', '@angular-devkit/build-angular'],
    plugins: [
      require('karma-jasmine'),
      require('karma-chrome-launcher'),
      require('karma-coverage'),
      require('@angular-devkit/build-angular/plugins/karma'),
    ],
    client: {
      jasmine: {},
      clearContext: false,
    },
    coverageReporter: {
      dir: require('path').join(__dirname, './coverage/diagnoseme'),
      subdir: '.',
      reporters: [{type: 'html'}, {type: 'text-summary'}],
    },
    // `ng test --code-coverage` adds the coverage reporter.
    reporters: ['progress'],
    browsers: ['ChromeHeadlessCustom'],
    customLaunchers: {
      ChromeHeadlessCustom: {
        base: 'ChromeHeadless',
        flags: [
          '--disable-gpu',
          '--disable-dev-shm-usage',
          ...(noSandbox ? ['--no-sandbox'] : []),
        ],
      },
    },
    restartOnFileChange: false,
    singleRun: true,
  });
};
