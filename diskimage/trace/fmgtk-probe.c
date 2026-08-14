/*
 * fmgtk-probe.c - bisect WHICH _fm_*_init call stalls pcmanfm under CheerpX
 * (plans/display-bug.md §2.9). GTK3 hello works, fm_init works, but the
 * libfm-gtk init sequence stalls. This probe replicates fm_gtk_init step by
 * step with ENTER/RET markers so the exact blocking call is identified.
 *
 * Build (i386 musl, Alpine 3.17):
 *   apk add gcc musl-dev pkgconf gtk+3.0-dev libfm-dev libfm-gtk-dev
 *   gcc -O2 -o fmgtk-probe fmgtk-probe.c $(pkg-config --cflags --libs gtk+-3.0 libfm-gtk)
 */
#include <stdio.h>
#include <dlfcn.h>
#include <gtk/gtk.h>

static void *libfm = NULL;
static void *libfmgtk = NULL;

static void *sym(const char *lib, const char *name)
{
	void *h = libfm;
	if (lib[0] == 'g')
		h = libfmgtk;
	void *p = dlsym(h, name);
	fprintf(stderr, "  dlsym %s from %s -> %p\n", name, lib, p);
	fflush(stderr);
	return p;
}

int main(int argc, char **argv)
{
	gtk_init(&argc, &argv);

	libfm = dlopen("libfm.so", RTLD_NOW);
	libfmgtk = dlopen("libfm-gtk.so", RTLD_NOW);
	fprintf(stderr, "libfm=%p libfm-gtk=%p\n", libfm, libfmgtk);
	fflush(stderr);
	if (!libfm || !libfmgtk)
	{
		fprintf(stderr, "dlopen failed: %s\n", dlerror());
		return 1;
	}

	typedef gpointer (*fm_config_new_fn)(void);
	typedef gboolean (*fm_init_fn)(gpointer);
	fm_config_new_fn fm_config_new = (fm_config_new_fn)sym("libfm", "fm_config_new");
	fm_init_fn fm_init = (fm_init_fn)sym("libfm", "fm_init");

	fprintf(stderr, "fm_config_new ENTER\n"); fflush(stderr);
	gpointer config = fm_config_new();
	fprintf(stderr, "fm_config_new RET=%p\n", config); fflush(stderr);

	fprintf(stderr, "fm_init ENTER\n"); fflush(stderr);
	gboolean ok = fm_init(config);
	fprintf(stderr, "fm_init RET=%d\n", ok); fflush(stderr);

	fprintf(stderr, "gtk_icon_theme_append_search_path ENTER\n"); fflush(stderr);
	gtk_icon_theme_append_search_path(gtk_icon_theme_get_default(), "/usr/share/libfm");
	fprintf(stderr, "gtk_icon_theme_append_search_path RET\n"); fflush(stderr);

	static const char *inits[] = {
		"_fm_icon_pixbuf_init",
		"_fm_thumbnail_init",
		"_fm_file_properties_init",
		"_fm_folder_model_init",
		"_fm_folder_view_init",
		"_fm_file_menu_init",
		NULL
	};
	for (int i = 0; inits[i]; i++)
	{
		typedef void (*init_fn)(void);
		init_fn f = (init_fn)sym("gtk", inits[i]);
		fprintf(stderr, "%s ENTER\n", inits[i]); fflush(stderr);
		if (f)
		{
			f();
			fprintf(stderr, "%s RET\n", inits[i]); fflush(stderr);
		}
		else
			fprintf(stderr, "%s NOT-FOUND\n", inits[i]); fflush(stderr);
	}

	fprintf(stderr, "FMGTK-PROBE-DONE\n");
	return 0;
}
