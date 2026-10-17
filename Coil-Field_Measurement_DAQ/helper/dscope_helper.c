/*
 * dscope_helper - headless capture server for DreamSourceLab DScope scopes.
 *
 * Built on DSView's libsigrok4DSL (GPLv3+). Reads one text command per line
 * on stdin and answers on stdout:
 *
 *   OK <json>            success (json may be {} )
 *   ERR <message>        failure
 *   FRAME <json>\n<raw>  one captured frame; <json>.nbytes raw bytes follow,
 *                        8-bit samples interleaved over the enabled channels
 *
 * Commands
 *   list                          devices (index, name)
 *   open <index | name-substring> activate a device and switch it to DSO mode
 *   info                          current settings as JSON
 *   set samplerate <Hz>
 *   set samples <n>               samples per channel per frame
 *   set trigsrc auto|ch0|ch1|and|or
 *   set slope rising|falling
 *   set trigpos <0..100>          pre-trigger share of the frame, percent
 *   set holdoff <n>               raw device units
 *   set margin <n>                trigger noise margin, raw ADC counts
 *   set ch <i> enable 0|1
 *   set ch <i> vdiv <mV>
 *   set ch <i> factor <n>         probe attenuation factor (1, 10, ...)
 *   set ch <i> coupling dc|ac
 *   set ch <i> offset <raw>       zero position, raw ADC counts
 *   set ch <i> trigraw <raw>      trigger level, raw ADC counts
 *   arm [settle_ms]               start acquiring, wait until running
 *   wait <timeout_ms> [any]       return the next (triggered) frame
 *   capture <timeout_ms> [any]    arm + wait
 *   disarm                        stop acquiring
 *   quit
 *
 * Library logs go to stderr. Set DSCOPE_LOG=0..5 to change the level.
 */
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include <glib.h>
#include "libsigrok.h"
#include "libsigrok-internal.h"

/* ------------------------------------------------------------------ */
/* protocol output                                                     */
/* ------------------------------------------------------------------ */
static FILE *proto = NULL;   /* private copy of the original stdout */

static void reply_ok(const char *json)
{
    fprintf(proto, "OK %s\n", json && *json ? json : "{}");
    fflush(proto);
}

static void reply_err(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    for (char *p = buf; *p; p++)
        if (*p == '\n' || *p == '\r') *p = ' ';
    fprintf(proto, "ERR %s\n", buf);
    fflush(proto);
}

/* ------------------------------------------------------------------ */
/* capture state shared with library callbacks                         */
/* ------------------------------------------------------------------ */
static pthread_mutex_t mtx = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond = PTHREAD_COND_INITIALIZER;

static int running = 0;          /* DS_EV_DEVICE_RUNNING seen          */
static int task_ended = 0;       /* collect task ended                 */
static int task_error = 0;       /* ended by error / detach            */
static int want_frame = 0;       /* waiting for a frame                */
static int want_any = 0;         /* accept untriggered frames          */
static int have_frame = 0;
static int frames_seen = 0;

static uint8_t *frame_buf = NULL;
static size_t frame_cap = 0;
static size_t frame_len = 0;
static int frame_samples = 0;
static int frame_trig = 0;
static int frame_trig_ch = 0;
static int frame_nch = 0;
static int frame_ch_index[8];
static int frame_hw_offset[8];
static double frame_time = 0;

static double now_s(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

static void on_event(int event)
{
    pthread_mutex_lock(&mtx);
    switch (event) {
    case DS_EV_DEVICE_RUNNING:
        running = 1;
        break;
    case DS_EV_COLLECT_TASK_END:
        task_ended = 1;
        running = 0;
        break;
    case DS_EV_COLLECT_TASK_END_BY_ERROR:
    case DS_EV_COLLECT_TASK_END_BY_DETACHED:
        task_ended = 1;
        task_error = 1;
        running = 0;
        break;
    default:
        break;
    }
    pthread_cond_broadcast(&cond);
    pthread_mutex_unlock(&mtx);
}

static void on_data(const struct sr_dev_inst *sdi,
                    const struct sr_datafeed_packet *packet)
{
    if (packet->type != SR_DF_DSO || packet->status != SR_PKT_OK ||
        packet->payload == NULL)
        return;

    const struct sr_datafeed_dso *dso = packet->payload;
    if (dso->num_samples <= 0)
        return;

    pthread_mutex_lock(&mtx);
    frames_seen++;
    if (want_frame && !have_frame && (want_any || dso->trig_flag)) {
        int nch = 0;
        for (GSList *l = sdi->channels; l; l = l->next) {
            struct sr_channel *ch = l->data;
            if (ch->type == SR_CHANNEL_DSO && ch->enabled && nch < 8) {
                frame_ch_index[nch] = ch->index;
                frame_hw_offset[nch] = ch->hw_offset;
                nch++;
            }
        }
        size_t need = (size_t)dso->num_samples * (size_t)nch;
        if (need > frame_cap) {
            uint8_t *nb = realloc(frame_buf, need);
            if (nb) {
                frame_buf = nb;
                frame_cap = need;
            }
        }
        if (need <= frame_cap && nch > 0) {
            memcpy(frame_buf, dso->data, need);
            frame_len = need;
            frame_samples = dso->num_samples;
            frame_nch = nch;
            frame_trig = dso->trig_flag ? 1 : 0;
            frame_trig_ch = dso->trig_ch;
            frame_time = now_s();
            have_frame = 1;
            want_frame = 0;
        }
    }
    pthread_cond_broadcast(&cond);
    pthread_mutex_unlock(&mtx);
}

/* ------------------------------------------------------------------ */
/* device helpers                                                      */
/* ------------------------------------------------------------------ */
static struct sr_dev_inst *active_sdi(void)
{
    struct ds_device_full_info info;
    if (!ds_have_actived_device())
        return NULL;
    memset(&info, 0, sizeof info);
    if (ds_get_actived_device_info(&info) != SR_OK)
        return NULL;
    return info.di;
}

static struct sr_channel *get_channel(int index)
{
    struct sr_dev_inst *sdi = active_sdi();
    if (!sdi)
        return NULL;
    for (GSList *l = sdi->channels; l; l = l->next) {
        struct sr_channel *ch = l->data;
        if (ch->index == index)
            return ch;
    }
    return NULL;
}

static int cfg_get_u64(struct sr_channel *ch, int key, uint64_t *out)
{
    GVariant *v = NULL;
    if (ds_get_actived_device_config(ch, NULL, key, &v) != SR_OK || !v)
        return 0;
    *out = g_variant_get_uint64(v);
    g_variant_unref(v);
    return 1;
}

static int cfg_get_u32(struct sr_channel *ch, int key, uint32_t *out)
{
    GVariant *v = NULL;
    if (ds_get_actived_device_config(ch, NULL, key, &v) != SR_OK || !v)
        return 0;
    *out = g_variant_get_uint32(v);
    g_variant_unref(v);
    return 1;
}

static int cfg_get_u16(struct sr_channel *ch, int key, uint16_t *out)
{
    GVariant *v = NULL;
    if (ds_get_actived_device_config(ch, NULL, key, &v) != SR_OK || !v)
        return 0;
    *out = g_variant_get_uint16(v);
    g_variant_unref(v);
    return 1;
}

static int cfg_get_byte(struct sr_channel *ch, int key, uint8_t *out)
{
    GVariant *v = NULL;
    if (ds_get_actived_device_config(ch, NULL, key, &v) != SR_OK || !v)
        return 0;
    *out = g_variant_get_byte(v);
    g_variant_unref(v);
    return 1;
}

static int cfg_set(struct sr_channel *ch, int key, GVariant *v)
{
    g_variant_ref_sink(v);
    int r = ds_set_actived_device_config(ch, NULL, key, v);
    g_variant_unref(v);
    return r;
}

static void json_u64_list(GString *s, int key, const char *field)
{
    GVariant *v = NULL, *arr;
    g_string_append_printf(s, "\"%s\":[", field);
    if (ds_get_actived_device_config_list(NULL, key, &v) == SR_OK && v) {
        arr = g_variant_lookup_value(v, field, G_VARIANT_TYPE("at"));
        if (arr) {
            gsize n = 0;
            const guint64 *p = g_variant_get_fixed_array(arr, &n,
                                                         sizeof(guint64));
            for (gsize i = 0; i < n; i++)
                g_string_append_printf(s, "%s%llu", i ? "," : "",
                                       (unsigned long long)p[i]);
            g_variant_unref(arr);
        }
        g_variant_unref(v);
    }
    g_string_append(s, "]");
}

static void json_escape(GString *s, const char *str)
{
    g_string_append_c(s, '"');
    for (; str && *str; str++) {
        if (*str == '"' || *str == '\\')
            g_string_append_c(s, '\\');
        if ((unsigned char)*str >= 0x20)
            g_string_append_c(s, *str);
    }
    g_string_append_c(s, '"');
}

static void cmd_info(void)
{
    struct sr_dev_inst *sdi = active_sdi();
    if (!sdi) {
        reply_err("no device open");
        return;
    }
    uint64_t sr = 0, lim = 0, maxsr = 0;
    uint32_t rmin = 1, rmax = 255;
    uint8_t bits = 8, tsrc = 0, slope = 0, tpos = 0, margin = 0;
    uint64_t holdoff = 0;

    cfg_get_u64(NULL, SR_CONF_SAMPLERATE, &sr);
    cfg_get_u64(NULL, SR_CONF_LIMIT_SAMPLES, &lim);
    cfg_get_u64(NULL, SR_CONF_MAX_DSO_SAMPLERATE, &maxsr);
    cfg_get_u32(NULL, SR_CONF_REF_MIN, &rmin);
    cfg_get_u32(NULL, SR_CONF_REF_MAX, &rmax);
    cfg_get_byte(NULL, SR_CONF_UNIT_BITS, &bits);
    cfg_get_byte(NULL, SR_CONF_TRIGGER_SOURCE, &tsrc);
    cfg_get_byte(NULL, SR_CONF_TRIGGER_SLOPE, &slope);
    cfg_get_byte(NULL, SR_CONF_HORIZ_TRIGGERPOS, &tpos);
    cfg_get_byte(NULL, SR_CONF_TRIGGER_MARGIN, &margin);
    cfg_get_u64(NULL, SR_CONF_TRIGGER_HOLDOFF, &holdoff);

    GString *s = g_string_new("{");
    g_string_append(s, "\"name\":");
    json_escape(s, sdi->name);
    g_string_append_printf(s,
        ",\"mode\":%d,\"dev_type\":%d,\"samplerate\":%llu,"
        "\"samples\":%llu,\"max_samplerate\":%llu,\"ref_min\":%u,"
        "\"ref_max\":%u,\"bits\":%u,\"trigsrc\":%u,\"slope\":%u,"
        "\"trigpos\":%u,\"margin\":%u,\"holdoff\":%llu,",
        sdi->mode, sdi->dev_type, (unsigned long long)sr,
        (unsigned long long)lim, (unsigned long long)maxsr, rmin, rmax,
        bits, tsrc, slope, tpos, margin, (unsigned long long)holdoff);
    json_u64_list(s, SR_CONF_SAMPLERATE, "samplerates");
    g_string_append(s, ",");
    json_u64_list(s, SR_CONF_PROBE_VDIV, "vdivs");
    g_string_append(s, ",\"channels\":[");
    int first = 1;
    for (GSList *l = sdi->channels; l; l = l->next) {
        struct sr_channel *ch = l->data;
        if (ch->type != SR_CHANNEL_DSO)
            continue;
        g_string_append_printf(s,
            "%s{\"index\":%d,\"enabled\":%d,\"vdiv\":%llu,\"factor\":%llu,"
            "\"coupling\":%u,\"offset\":%u,\"hw_offset\":%u,"
            "\"trig_value\":%u}",
            first ? "" : ",", ch->index, ch->enabled ? 1 : 0,
            (unsigned long long)ch->vdiv, (unsigned long long)ch->vfactor,
            ch->coupling, ch->offset, ch->hw_offset, ch->trig_value);
        first = 0;
    }
    g_string_append(s, "]}");
    reply_ok(s->str);
    g_string_free(s, TRUE);
}

static void cmd_list(void)
{
    struct ds_device_base_info *list = NULL;
    int n = 0;
    if (ds_get_device_list(&list, &n) != SR_OK) {
        reply_err("cannot read device list");
        return;
    }
    GString *s = g_string_new("{\"devices\":[");
    for (int i = 0; i < n; i++) {
        g_string_append_printf(s, "%s{\"index\":%d,\"name\":", i ? "," : "", i);
        json_escape(s, list[i].name);
        g_string_append(s, "}");
    }
    g_string_append(s, "]}");
    reply_ok(s->str);
    g_string_free(s, TRUE);
    g_free(list);
}

static int wait_initialized(int timeout_ms)
{
    double t_end = now_s() + timeout_ms / 1000.0;
    int st = SR_ST_INITIALIZING;
    while (now_s() < t_end) {
        if (ds_get_actived_device_init_status(&st) == SR_OK &&
            st != SR_ST_INITIALIZING)
            return 1;
        g_usleep(50000);
    }
    return 0;
}

static void cmd_open(const char *arg)
{
    struct ds_device_base_info *list = NULL;
    int n = 0, pick = -1;

    /* a freshly plugged device may still be re-enumerating */
    for (int attempt = 0; attempt < 30; attempt++) {
        g_free(list);
        list = NULL;
        ds_get_device_list(&list, &n);
        if (arg && *arg) {
            char *end;
            long idx = strtol(arg, &end, 10);
            if (*end == '\0' && idx >= 0 && idx < n) {
                pick = (int)idx;
            } else {
                for (int i = 0; i < n; i++)
                    if (strcasestr(list[i].name, arg)) {
                        pick = i;
                        break;
                    }
            }
        } else {
            for (int i = 0; i < n; i++)       /* prefer real hardware */
                if (strcasestr(list[i].name, "scope")) {
                    pick = i;
                    break;
                }
        }
        if (pick >= 0)
            break;
        g_usleep(200000);
    }
    if (pick < 0) {
        reply_err("device '%s' not found (run 'list')", arg ? arg : "");
        g_free(list);
        return;
    }

    int r = ds_active_device(list[pick].handle);
    g_free(list);
    if (r != SR_OK) {
        reply_err("could not open device (error %d). Is DSView closed? "
                  "Are the firmware files found?", r);
        return;
    }
    if (!wait_initialized(15000)) {
        reply_err("device did not finish initialising");
        return;
    }
    if (ds_get_actived_device_mode() != DSO) {
        r = cfg_set(NULL, SR_CONF_DEVICE_MODE, g_variant_new_int16(DSO));
        if (r != SR_OK) {
            reply_err("could not switch to oscilloscope (DSO) mode");
            return;
        }
    }
    cfg_set(NULL, SR_CONF_INSTANT, g_variant_new_boolean(FALSE));
    cmd_info();
}

static int parse_trigsrc(const char *v)
{
    if (!strcasecmp(v, "auto")) return DSO_TRIGGER_AUTO;
    if (!strcasecmp(v, "ch0")) return DSO_TRIGGER_CH0;
    if (!strcasecmp(v, "ch1")) return DSO_TRIGGER_CH1;
    if (!strcasecmp(v, "and")) return DSO_TRIGGER_CH0A1;
    if (!strcasecmp(v, "or")) return DSO_TRIGGER_CH0O1;
    return -1;
}

static void cmd_set(char **tok, int ntok)
{
    int r = SR_ERR;
    if (!active_sdi()) {
        reply_err("no device open");
        return;
    }
    if (ds_is_collecting()) {
        reply_err("disarm before changing settings");
        return;
    }
    if (ntok < 3) {
        reply_err("usage: set <key> <value>");
        return;
    }
    const char *key = tok[1];
    const char *val = tok[2];

    if (!strcmp(key, "samplerate")) {
        r = cfg_set(NULL, SR_CONF_SAMPLERATE,
                    g_variant_new_uint64(strtoull(val, NULL, 10)));
    } else if (!strcmp(key, "samples")) {
        r = cfg_set(NULL, SR_CONF_LIMIT_SAMPLES,
                    g_variant_new_uint64(strtoull(val, NULL, 10)));
    } else if (!strcmp(key, "trigsrc")) {
        int s = parse_trigsrc(val);
        if (s < 0) {
            reply_err("trigsrc must be auto, ch0, ch1, and, or");
            return;
        }
        r = cfg_set(NULL, SR_CONF_TRIGGER_SOURCE, g_variant_new_byte(s));
    } else if (!strcmp(key, "slope")) {
        int s = !strcasecmp(val, "falling") ? DSO_TRIGGER_FALLING
                                            : DSO_TRIGGER_RISING;
        r = cfg_set(NULL, SR_CONF_TRIGGER_SLOPE, g_variant_new_byte(s));
    } else if (!strcmp(key, "trigpos")) {
        int p = atoi(val);
        p = p < 0 ? 0 : p > 100 ? 100 : p;
        r = cfg_set(NULL, SR_CONF_HORIZ_TRIGGERPOS, g_variant_new_byte(p));
    } else if (!strcmp(key, "holdoff")) {
        r = cfg_set(NULL, SR_CONF_TRIGGER_HOLDOFF,
                    g_variant_new_uint64(strtoull(val, NULL, 10)));
    } else if (!strcmp(key, "margin")) {
        r = cfg_set(NULL, SR_CONF_TRIGGER_MARGIN,
                    g_variant_new_byte(atoi(val)));
    } else if (!strcmp(key, "ch")) {
        if (ntok < 5) {
            reply_err("usage: set ch <index> <field> <value>");
            return;
        }
        int idx = atoi(tok[2]);
        const char *field = tok[3];
        const char *v = tok[4];
        struct sr_channel *ch = get_channel(idx);
        if (!ch) {
            reply_err("no channel %d", idx);
            return;
        }
        if (!strcmp(field, "enable")) {
            r = ds_enable_device_channel_index(idx, atoi(v) ? TRUE : FALSE);
        } else if (!strcmp(field, "vdiv")) {
            r = cfg_set(ch, SR_CONF_PROBE_VDIV,
                        g_variant_new_uint64(strtoull(v, NULL, 10)));
        } else if (!strcmp(field, "factor")) {
            r = cfg_set(ch, SR_CONF_PROBE_FACTOR,
                        g_variant_new_uint64(strtoull(v, NULL, 10)));
        } else if (!strcmp(field, "coupling")) {
            int c = !strcasecmp(v, "ac") ? SR_AC_COUPLING : SR_DC_COUPLING;
            r = cfg_set(ch, SR_CONF_PROBE_COUPLING, g_variant_new_byte(c));
        } else if (!strcmp(field, "offset")) {
            r = cfg_set(ch, SR_CONF_PROBE_OFFSET,
                        g_variant_new_uint16(atoi(v)));
        } else if (!strcmp(field, "trigraw")) {
            int t = atoi(v);
            t = t < 0 ? 0 : t > 255 ? 255 : t;
            r = cfg_set(ch, SR_CONF_TRIGGER_VALUE, g_variant_new_byte(t));
        } else {
            reply_err("unknown channel field '%s'", field);
            return;
        }
    } else {
        reply_err("unknown setting '%s'", key);
        return;
    }

    if (r != SR_OK)
        reply_err("setting %s failed (error %d)", key, r);
    else
        reply_ok("{}");
}

static int do_arm(int settle_ms, char *err, size_t errlen)
{
    if (!active_sdi()) {
        snprintf(err, errlen, "no device open");
        return 0;
    }
    if (ds_is_collecting())
        return 1;               /* already armed */

    /* make sure the trigger position matches the current frame length */
    uint8_t tpos = 0;
    if (cfg_get_byte(NULL, SR_CONF_HORIZ_TRIGGERPOS, &tpos))
        cfg_set(NULL, SR_CONF_HORIZ_TRIGGERPOS, g_variant_new_byte(tpos));
    cfg_set(NULL, SR_CONF_INSTANT, g_variant_new_boolean(FALSE));

    pthread_mutex_lock(&mtx);
    running = 0;
    task_ended = 0;
    task_error = 0;
    have_frame = 0;
    want_frame = 0;
    pthread_mutex_unlock(&mtx);

    int r = ds_start_collect();
    if (r != SR_OK) {
        snprintf(err, errlen, "start failed (error %d)", r);
        return 0;
    }

    double t_end = now_s() + 5.0;
    pthread_mutex_lock(&mtx);
    while (!running && !task_ended && now_s() < t_end) {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_nsec += 20 * 1000000L;
        if (ts.tv_nsec >= 1000000000L) {
            ts.tv_sec++;
            ts.tv_nsec -= 1000000000L;
        }
        pthread_cond_timedwait(&cond, &mtx, &ts);
    }
    int ok = running;
    int ended = task_ended;
    pthread_mutex_unlock(&mtx);

    if (!ok) {
        snprintf(err, errlen, ended ? "acquisition ended immediately"
                                    : "device did not start");
        if (ds_is_collecting())
            ds_stop_collect();
        return 0;
    }
    if (settle_ms > 0)
        g_usleep((gulong)settle_ms * 1000);
    return 1;
}

static void do_disarm(void)
{
    if (ds_is_collecting())
        ds_stop_collect();
    pthread_mutex_lock(&mtx);
    want_frame = 0;
    running = 0;
    pthread_mutex_unlock(&mtx);
}

static void send_frame(void)
{
    struct sr_dev_inst *sdi = active_sdi();
    uint64_t sr = 0;
    uint32_t rmin = 1, rmax = 255;
    uint8_t tpos = 0;
    cfg_get_u64(NULL, SR_CONF_SAMPLERATE, &sr);
    cfg_get_u32(NULL, SR_CONF_REF_MIN, &rmin);
    cfg_get_u32(NULL, SR_CONF_REF_MAX, &rmax);
    cfg_get_byte(NULL, SR_CONF_HORIZ_TRIGGERPOS, &tpos);

    GString *s = g_string_new("{");
    g_string_append_printf(s,
        "\"num_samples\":%d,\"nch\":%d,\"nbytes\":%zu,\"samplerate\":%llu,"
        "\"trig_flag\":%d,\"trig_ch\":%d,\"trigpos\":%u,\"ref_min\":%u,"
        "\"ref_max\":%u,\"host_time\":%.6f,\"channels\":[",
        frame_samples, frame_nch, frame_len, (unsigned long long)sr,
        frame_trig, frame_trig_ch, tpos, rmin, rmax, frame_time);
    for (int i = 0; i < frame_nch; i++) {
        struct sr_channel *ch = get_channel(frame_ch_index[i]);
        g_string_append_printf(s,
            "%s{\"index\":%d,\"hw_offset\":%d,\"offset\":%u,\"vdiv\":%llu,"
            "\"factor\":%llu,\"coupling\":%u}",
            i ? "," : "", frame_ch_index[i], frame_hw_offset[i],
            ch ? ch->offset : 0,
            (unsigned long long)(ch ? ch->vdiv : 0),
            (unsigned long long)(ch ? ch->vfactor : 1),
            ch ? ch->coupling : 0);
    }
    g_string_append(s, "]}");
    (void)sdi;

    fprintf(proto, "FRAME %s\n", s->str);
    fwrite(frame_buf, 1, frame_len, proto);
    fflush(proto);
    g_string_free(s, TRUE);
}

static void cmd_wait(int timeout_ms, int any)
{
    if (!ds_is_collecting()) {
        reply_err("not armed");
        return;
    }
    pthread_mutex_lock(&mtx);
    have_frame = 0;
    want_any = any;
    want_frame = 1;
    double t_end = now_s() + timeout_ms / 1000.0;
    while (!have_frame && !task_ended && now_s() < t_end) {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_nsec += 20 * 1000000L;
        if (ts.tv_nsec >= 1000000000L) {
            ts.tv_sec++;
            ts.tv_nsec -= 1000000000L;
        }
        pthread_cond_timedwait(&cond, &mtx, &ts);
    }
    int got = have_frame;
    int ended = task_ended, err = task_error;
    want_frame = 0;
    pthread_mutex_unlock(&mtx);

    /* stop right away so the next frame starts from a clean arm */
    do_disarm();

    if (got)
        send_frame();
    else if (ended)
        reply_err(err ? "acquisition stopped with an error"
                      : "acquisition ended before a frame arrived");
    else
        reply_err("timeout: no trigger within %d ms", timeout_ms);
}

/* ------------------------------------------------------------------ */
/* firmware directory                                                  */
/* ------------------------------------------------------------------ */
static int dir_has(const char *dir, const char *file)
{
    char path[1024];
    struct stat st;
    snprintf(path, sizeof path, "%s/%s", dir, file);
    return stat(path, &st) == 0;
}

static const char *find_res_dir(const char *cli)
{
    static const char *cands[] = {
        "/Applications/DSView.app/Contents/MacOS/res",
        "/Applications/DSView.app/Contents/Resources/res",
        "/Applications/DSView.app/Contents/share/DSView/res",
        "/usr/local/share/DSView/res",
        "/usr/share/DSView/res",
        "/opt/homebrew/share/DSView/res",
        "C:/Program Files/DSView/res",
        "./res",
        NULL};
    if (cli && *cli)
        return cli;
    const char *env = getenv("DSCOPE_RES");
    if (env && *env)
        return env;
    for (int i = 0; cands[i]; i++)
        if (dir_has(cands[i], "DSCope20.bin"))
            return cands[i];
    return "./res";
}

/* ------------------------------------------------------------------ */
/* main loop                                                           */
/* ------------------------------------------------------------------ */
int main(int argc, char **argv)
{
    const char *res_cli = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--res") && i + 1 < argc)
            res_cli = argv[++i];
        else if (!strcmp(argv[i], "--help")) {
            fprintf(stderr, "usage: %s [--res <DSView res dir>]\n"
                            "commands are read from stdin, see source\n",
                    argv[0]);
            return 0;
        }
    }

    /* keep the protocol on the real stdout, send everything else to stderr */
    int proto_fd = dup(STDOUT_FILENO);
    proto = fdopen(proto_fd, "wb");
    dup2(STDERR_FILENO, STDOUT_FILENO);

    const char *lvl = getenv("DSCOPE_LOG");
    ds_log_level(lvl ? atoi(lvl) : 1);

    const char *res = find_res_dir(res_cli);
    fprintf(stderr, "dscope_helper: firmware/resource dir: %s\n", res);
    ds_set_firmware_resource_dir(res);
    ds_set_user_data_dir(res);
    ds_set_event_callback(on_event);
    ds_set_datafeed_callback(on_data);

    if (ds_lib_init() != SR_OK) {
        reply_err("library init failed");
        return 1;
    }
    reply_ok("{\"ready\":1}");

    char line[1024];
    while (fgets(line, sizeof line, stdin)) {
        char *tok[8];
        int ntok = 0;
        char *save = NULL;
        for (char *t = strtok_r(line, " \t\r\n", &save); t && ntok < 8;
             t = strtok_r(NULL, " \t\r\n", &save))
            tok[ntok++] = t;
        if (ntok == 0)
            continue;

        const char *c = tok[0];
        char err[256];
        if (!strcmp(c, "quit") || !strcmp(c, "exit")) {
            break;
        } else if (!strcmp(c, "list")) {
            cmd_list();
        } else if (!strcmp(c, "open")) {
            cmd_open(ntok > 1 ? tok[1] : NULL);
        } else if (!strcmp(c, "info")) {
            cmd_info();
        } else if (!strcmp(c, "set")) {
            cmd_set(tok, ntok);
        } else if (!strcmp(c, "arm")) {
            if (do_arm(ntok > 1 ? atoi(tok[1]) : 30, err, sizeof err))
                reply_ok("{\"armed\":1}");
            else
                reply_err("%s", err);
        } else if (!strcmp(c, "wait")) {
            cmd_wait(ntok > 1 ? atoi(tok[1]) : 5000,
                     ntok > 2 && !strcmp(tok[2], "any"));
        } else if (!strcmp(c, "capture")) {
            if (do_arm(30, err, sizeof err))
                cmd_wait(ntok > 1 ? atoi(tok[1]) : 5000,
                         ntok > 2 && !strcmp(tok[2], "any"));
            else
                reply_err("%s", err);
        } else if (!strcmp(c, "disarm")) {
            do_disarm();
            reply_ok("{}");
        } else if (!strcmp(c, "ping")) {
            reply_ok("{\"pong\":1}");
        } else {
            reply_err("unknown command '%s'", c);
        }
    }

    do_disarm();
    if (ds_have_actived_device())
        ds_release_actived_device();
    ds_lib_exit();
    return 0;
}
