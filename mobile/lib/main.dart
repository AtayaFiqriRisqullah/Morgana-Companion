// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'core/app_state.dart';
import 'screens/home_shell.dart';
import 'theme/p5_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Color(0xFF08080D),
    statusBarIconBrightness: Brightness.light,
    systemNavigationBarColor: Color(0xFF09090F),
    systemNavigationBarIconBrightness: Brightness.light,
  ));
  runApp(const MorganaCompanionApp());
}

class MorganaCompanionApp extends StatefulWidget {
  final MorganaAppState? appState;

  const MorganaCompanionApp({super.key, this.appState});

  @override
  State<MorganaCompanionApp> createState() => _MorganaCompanionAppState();
}

class _MorganaCompanionAppState extends State<MorganaCompanionApp> {
  late final MorganaAppState _app;
  late final bool _ownsApp;

  @override
  void initState() {
    super.initState();
    _ownsApp = widget.appState == null;
    _app = widget.appState ?? MorganaAppState();
    if (_ownsApp) {
      _app.init();
    }
  }

  @override
  void dispose() {
    if (_ownsApp) {
      _app.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Morgana Companion',
      debugShowCheckedModeBanner: false,
      theme: buildP5Theme(),
      home: HomeShell(app: _app),
    );
  }
}
