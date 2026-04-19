Name:           evim-editor
Version:        1.0.0
Release:        1%{?dist}
Summary:        Modal CLI text editor with Vim keybindings
License:        MIT
URL:            https://github.com/tortr-rs/Editor-VIM
Source0:        %{url}/archive/v%{version}.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.8

%description
EVim is a feature-rich terminal text editor with Vim-style modal editing,
LSP support, plugin system, 24 color themes, syntax highlighting for 55+
languages, file explorer, integrated terminal, and more.

%prep
%autosetup -n Editor-VIM-%{version}

%install
mkdir -p %{buildroot}%{_libdir}/evim
mkdir -p %{buildroot}%{_bindir}
install -Dm755 evim.py %{buildroot}%{_libdir}/evim/evim.py
cat > %{buildroot}%{_bindir}/evim <<'EOF'
#!/bin/sh
exec python3 %{_libdir}/evim/evim.py "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/evim

%files
%license LICENSE
%doc README.md
%{_libdir}/evim/evim.py
%{_bindir}/evim

%changelog
* Sun Apr 19 2026 tortr-rs - 1.0.0-1
- Initial package
