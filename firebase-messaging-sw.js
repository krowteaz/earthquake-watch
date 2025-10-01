importScripts("https://www.gstatic.com/firebasejs/9.6.1/firebase-app.js");
importScripts("https://www.gstatic.com/firebasejs/9.6.1/firebase-messaging.js");

firebase.initializeApp({
  apiKey: "AIzaSyB3uk0a4RSU9EcOJLadaWYvX_v8O82YWbs",
  authDomain: "earthquakewatch-1f530.firebaseapp.com",
  projectId: "earthquakewatch-1f530",
  storageBucket: "earthquakewatch-1f530.firebasestorage.app",
  messagingSenderId: "550569254609",
  appId: "1:550569254609:web:4b4ece5b41b577f7f0eff0"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  console.log("[firebase-messaging-sw.js] Received background message: ", payload);
  const notificationTitle = payload.notification.title;
  const notificationOptions = {
    body: payload.notification.body,
    icon: "/icon.png" // optional
  };
  self.registration.showNotification(notificationTitle, notificationOptions);
});
