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

RUN npm install -g npm@latest
RUN npm install --ignore-scripts

# install and cache app dependencies
# add app

COPY src/ui/ /app/
RUN rm -rf /app/src/protos
WORKDIR /app/src/protos
COPY --chown=diagnoseme src/generated/*.ts /app/src/protos/

WORKDIR /app
RUN ng build --output-path=dist --output-hashing=all --configuration=production

#####################################################################

FROM nginx:alpine

ARG UID=246

RUN addgroup --gid $UID diagnoseme && adduser --ingroup diagnoseme --uid $UID \
     --disabled-password --gecos "" diagnoseme

RUN mkdir -p /var/cache/nginx/client_temp
RUN mkdir -p /var/cache/nginx/proxy_temp
RUN chown -R diagnoseme /var/cache/nginx
RUN chmod -R uog+rw /var/run

# copy artifact build from the 'build environment'
COPY --chown=diagnoseme --from=build /app/dist/browser /etc/nginx/html/

COPY --chown=diagnoseme src/ui/nginx.conf /etc/nginx
RUN rm -f /etc/nginx/conf.d/*
RUN rm -f /docker-entrypoint.d/*
RUN rm -f /var/log/nginx/*
RUN chmod uog+rw /var/log/nginx
RUN chown -R diagnoseme:diagnoseme /var/log/nginx


EXPOSE 6010

USER diagnoseme

# run nginx
CMD ["nginx", "-g", "daemon off;"]
