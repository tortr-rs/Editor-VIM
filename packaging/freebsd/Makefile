PORTNAME=	evim-editor
DISTVERSION=	1.0.0
CATEGORIES=	editors

MAINTAINER=	tortr-rs@github.com
COMMENT=	Modal CLI text editor with Vim keybindings
WWW=		https://github.com/tortr-rs/Editor-VIM

LICENSE=	MIT

USE_GITHUB=	yes
GH_ACCOUNT=	tortr-rs
GH_PROJECT=	Editor-VIM
GH_TAGNAME=	v${DISTVERSION}

RUN_DEPENDS=	python3:lang/python3

NO_BUILD=	yes
NO_ARCH=	yes

do-install:
	${MKDIR} ${STAGEDIR}${PREFIX}/lib/evim
	${INSTALL_SCRIPT} ${WRKSRC}/evim.py ${STAGEDIR}${PREFIX}/lib/evim/evim.py
	${INSTALL_SCRIPT} /dev/stdin ${STAGEDIR}${PREFIX}/bin/evim <<'EOF'
#!/bin/sh
exec python3 ${PREFIX}/lib/evim/evim.py "$$@"
EOF

.include <bsd.port.mk>
