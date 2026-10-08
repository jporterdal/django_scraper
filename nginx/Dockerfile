FROM nginx:alpine

# Remove the default placeholder config
#RUN rm /etc/nginx/conf.d/default.conf

# Copy your custom proxy configuration into the container
#COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/templates/default.conf.template

RUN rm /etc/nginx/conf.d/default.conf


# Expose port 80 to Railway's internal proxy mesh
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
