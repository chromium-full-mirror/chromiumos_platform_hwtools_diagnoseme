# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

######################################################################
FROM node:24 AS build

WORKDIR /app

# add `/app/node_modules/.bin` to $PATH
ENV PATH=/app/node_modules/.bin:$PATH


# install and cache app dependencies
# add app
COPY src/ui/package.json /app

RUN npm install --ignore-scripts --legacy-peer-deps

# install and cache app dependencies
# add app

COPY src/ui/ /app/

WORKDIR /app
# DiagnoseMe edition (see editions.json and angular.json configurations).
ARG EDITION=all
RUN ng build --output-path=dist --output-hashing=all --configuration=production,${EDITION}

#####################################################################

FROM nginx:alpine

RUN mkdir -p /var/cache/nginx/client_temp
RUN mkdir -p /var/cache/nginx/proxy_temp
RUN chmod uog+rw /var/run

# copy artifact build from the 'build environment'
COPY --from=build /app/dist/browser /etc/nginx/html/

COPY src/ui/nginx.conf /etc/nginx
RUN rm -f /etc/nginx/conf.d/*
RUN rm -f /docker-entrypoint.d/*
RUN rm -f /var/log/nginx/*
RUN chmod uog+rw /var/log/nginx

COPY src/ui/src/assets/ /etc/nginx/html/assets/
RUN chmod -R 755 /etc/nginx/html



EXPOSE 6010

#USER diagnoseme

# run nginx
CMD ["nginx", "-g", "daemon off;"]
