import networks
import torch
import torch.nn as nn
import models_vit
def interpolate_pos_embed(model, checkpoint_model):
  if 'pos_embed' in checkpoint_model:
    pos_embed_checkpoint = checkpoint_model['pos_embed']
    embedding_size = pos_embed_checkpoint.shape[-1]
    num_patches = model.patch_embed.num_patches
    num_extra_tokens = model.pos_embed.shape[-2] - num_patches
    # height (== width) for the checkpoint position embedding
    orig_size = int((pos_embed_checkpoint.shape[-2] - num_extra_tokens) ** 0.5)
    # height (== width) for the new position embedding
    new_size = int(num_patches ** 0.5)
    # class_token and dist_token are kept unchanged
    if orig_size != new_size:
      print("Position interpolate from %dx%d to %dx%d" % (orig_size, orig_size, new_size, new_size))
      extra_tokens = pos_embed_checkpoint[:, :num_extra_tokens]
      # only the position tokens are interpolated
      pos_tokens = pos_embed_checkpoint[:, num_extra_tokens:]
      pos_tokens = pos_tokens.reshape(-1, orig_size, orig_size, embedding_size).permute(0, 3, 1, 2)
      pos_tokens = torch.nn.functional.interpolate(pos_tokens, size=(new_size, new_size), mode='bicubic', align_corners=False)
      pos_tokens = pos_tokens.permute(0, 2, 3, 1).flatten(1, 2)
      new_pos_embed = torch.cat((extra_tokens, pos_tokens), dim=1)
      checkpoint_model['pos_embed'] = new_pos_embed
  return(checkpoint_model)
class DRIT(nn.Module):
  def __init__(self, opts):
    super(DRIT, self).__init__()

    # parameters
    lr = 0.0001
    lr_dcontent = lr/2.5
    self.nz = 8
    self.concat = opts.concat
    self.ms = opts.ms

    # 2-scale discriminators
    self.disA = networks.MultiScaleDis(opts.input_dim_a, 2, norm=opts.dis_norm, sn=opts.dis_spectral_norm)
    self.disB = networks.MultiScaleDis(opts.input_dim_b, 2, norm=opts.dis_norm, sn=opts.dis_spectral_norm)
    self.disA2 = networks.MultiScaleDis(opts.input_dim_a, 2, norm=opts.dis_norm, sn=opts.dis_spectral_norm)
    self.disB2 = networks.MultiScaleDis(opts.input_dim_b, 2, norm=opts.dis_norm, sn=opts.dis_spectral_norm)
    self.disContent = networks.Dis_content()

    # encoders
    self.enc_c_a = models_vit.__dict__['vit_large_patch16'](
        num_classes=8,
        drop_path_rate=0.1
    )
    self.enc_c_b = models_vit.__dict__['vit_large_patch16'](
        num_classes=8,
        drop_path_rate=0.1
    )
    # enc_share = []
    # for i in range(0, 1):
    #   # enc_share += [nn.Linear(1024,256,bias=True)]
    #   enc_share += [networks.INSResBlock(256, 256)]
    #   enc_share += [networks.GaussianNoiseLayer()]
    #   self.conv_share = nn.Sequential(*enc_share)
    #self.enc_c = networks.E_content(opts.input_dim_a, opts.input_dim_b)
    checkpoint = torch.load("/home/ayg2kor/mae_visualize_vit_large.pth", map_location='cpu')
    self.conv_share = networks.E_content_share(opts.input_dim_a,opts.input_dim_b)
    print("Load pre-trained checkpoint from: /home/ayg2kor/mae_visualize_vit_large.pth" )
    checkpoint_model = checkpoint['model']
    state_dict = self.enc_c_a.state_dict()
    for k in ['head.weight', 'head.bias']:
        if k in checkpoint_model and checkpoint_model[k].shape != state_dict[k].shape:
            print(f"Removing key {k} from pretrained checkpoint")
            del checkpoint_model[k]

    # interpolate position embedding
    checkpoint_model = interpolate_pos_embed(self.enc_c_a, checkpoint_model)

    # load pre-trained model
    msg = self.enc_c_a.load_state_dict(checkpoint_model, strict=False)
    print("Load pre-trained checkpoint from: /home/ayg2kor/mae_visualize_vit_large.pth" )
    checkpoint_model = checkpoint['model']
    print(checkpoint_model.keys())
    state_dict = self.enc_c_b.state_dict()
    for k in ['head.weight', 'head.bias']:
        if k in checkpoint_model and checkpoint_model[k].shape != state_dict[k].shape:
            print(f"Removing key {k} from pretrained checkpoint")
            del checkpoint_model[k]

    # interpolate position embedding
    checkpoint_model = interpolate_pos_embed(self.enc_c_b, checkpoint_model)

    # load pre-trained model
    msg = self.enc_c_b.load_state_dict(checkpoint_model, strict=False)
    if self.concat:
      self.enc_a = networks.E_attr_concat(opts.input_dim_a, opts.input_dim_b, self.nz, \
          norm_layer=None, nl_layer=networks.get_non_linearity(layer_type='lrelu'))
    else:
      self.enc_a = networks.E_attr(opts.input_dim_a, opts.input_dim_b, self.nz)

    # generator
    if self.concat:
      self.gen = networks.G_concat(opts.input_dim_a, opts.input_dim_b, nz=self.nz)
    else:
      self.gen = networks.G(opts.input_dim_a, opts.input_dim_b, nz=self.nz)

    # optimizers
    self.disA_opt = torch.optim.Adam(self.disA.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.disB_opt = torch.optim.Adam(self.disB.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.disA2_opt = torch.optim.Adam(self.disA2.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.disB2_opt = torch.optim.Adam(self.disB2.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.disContent_opt = torch.optim.Adam(self.disContent.parameters(), lr=lr_dcontent, betas=(0.5, 0.999), weight_decay=0.0001)
    self.enc_c_a_opt = torch.optim.Adam(self.enc_c_a.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.enc_c_b_opt = torch.optim.Adam(self.enc_c_b.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.conv_share_opt = torch.optim.Adam(self.conv_share.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.enc_a_opt = torch.optim.Adam(self.enc_a.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)
    self.gen_opt = torch.optim.Adam(self.gen.parameters(), lr=lr, betas=(0.5, 0.999), weight_decay=0.0001)

    # Setup the loss function for training
    self.criterionL1 = torch.nn.L1Loss()
    self.criterionL2 = torch.nn.MSELoss()

  def initialize(self):
    self.disA.apply(networks.gaussian_weights_init)
    self.disB.apply(networks.gaussian_weights_init)
    self.disA2.apply(networks.gaussian_weights_init)
    self.disB2.apply(networks.gaussian_weights_init)
    self.disContent.apply(networks.gaussian_weights_init)
    self.gen.apply(networks.gaussian_weights_init)
    # self.enc_c_a.apply(networks.gaussian_weights_init)
    # self.enc_c_b.apply(networks.gaussian_weights_init)
    self.conv_share.apply(networks.gaussian_weights_init)
    self.enc_a.apply(networks.gaussian_weights_init)

  def set_scheduler(self, opts, last_ep=0):
    self.disA_sch = networks.get_scheduler(self.disA_opt, opts, last_ep)
    self.disB_sch = networks.get_scheduler(self.disB_opt, opts, last_ep)
    self.disA2_sch = networks.get_scheduler(self.disA2_opt, opts, last_ep)
    self.disB2_sch = networks.get_scheduler(self.disB2_opt, opts, last_ep)
    self.disContent_sch = networks.get_scheduler(self.disContent_opt, opts, last_ep)
    self.enc_c_a_sch = networks.get_scheduler(self.enc_c_a_opt, opts, last_ep)
    self.enc_c_b_sch = networks.get_scheduler(self.enc_c_b_opt, opts, last_ep)
    self.conv_share_sch = networks.get_scheduler(self.conv_share_opt, opts, last_ep)
    self.enc_a_sch = networks.get_scheduler(self.enc_a_opt, opts, last_ep)
    self.gen_sch = networks.get_scheduler(self.gen_opt, opts, last_ep)

  def setgpu(self, gpu1,gpu2):
    self.gpu = gpu1
    self.gpu2 = gpu2
    self.disA.cuda(self.gpu)
    self.disB.cuda(self.gpu)
    self.disA2.cuda(self.gpu)
    self.disB2.cuda(self.gpu)
    self.disContent.cuda(self.gpu)
    self.enc_c_a.cuda(self.gpu2)
    self.enc_c_b.cuda(self.gpu2)
    self.enc_a.cuda(self.gpu)
    self.gen.cuda(self.gpu)
    self.conv_share.cuda(self.gpu)

  def get_z_random(self, batchSize, nz, random_type='gauss'):
    z = torch.randn(batchSize, nz).cuda(self.gpu)
    return z

  def test_forward(self, image, a2b=True):
    self.z_random = self.get_z_random(image.size(0), self.nz, 'gauss')
    if a2b:
      self.z_content = self.enc_c_a.forward(image)
      output = self.gen.forward_b(self.z_content, self.z_random)
    else:
      self.z_content = self.enc_c_b.forward(image)
      output = self.gen.forward_a(self.z_content, self.z_random)
    return output

  def test_forward_transfer(self, image_a, image_b, a2b=True):
    self.z_content_a = self.enc_c_a.forward(image_a)
    self.z_content_b = self.enc_c_b.forward(image_b)
    if self.concat:
      self.mu_a, self.logvar_a, self.mu_b, self.logvar_b = self.enc_a.forward(image_a, image_b)
      std_a = self.logvar_a.mul(0.5).exp_()
      eps = self.get_z_random(std_a.size(0), std_a.size(1), 'gauss')
      self.z_attr_a = eps.mul(std_a).add_(self.mu_a)
      std_b = self.logvar_b.mul(0.5).exp_()
      eps = self.get_z_random(std_b.size(0), std_b.size(1), 'gauss')
      self.z_attr_b = eps.mul(std_b).add_(self.mu_b)
    else:
      self.z_attr_a, self.z_attr_b = self.enc_a.forward(image_a, image_b)
    if a2b:
      output = self.gen.forward_b(self.z_content_a, self.z_attr_b)
    else:
      output = self.gen.forward_a(self.z_content_b, self.z_attr_a)
    return output

  def forward(self):
    # input images
    half_size = 1
    real_A = self.input_A
    real_B = self.input_B
    self.real_A_encoded = real_A[0:half_size]
    self.real_A_random = real_A[half_size:]
    self.real_B_encoded = real_B[0:half_size]
    self.real_B_random = real_B[half_size:]

    # get encoded z_c
    self.real_A_encoded = self.real_A_encoded.cuda(self.gpu2)
    self.real_B_encoded = self.real_A_encoded.cuda(self.gpu2)
    z_content_a_1 = self.enc_c_a.forward_features(self.real_A_encoded)
    z_content_b_1 = self.enc_c_b.forward_features(self.real_B_encoded)
    self.real_A_encoded = self.real_A_encoded.cuda(self.gpu)
    self.real_B_encoded = self.real_A_encoded.cuda(self.gpu)
    self.z_content_a,self.z_content_b = self.conv_share(z_content_a_1.cuda(self.gpu),z_content_b_1.cuda(self.gpu))
       

    # get encoded z_a
    if self.concat:
      self.mu_a, self.logvar_a, self.mu_b, self.logvar_b = self.enc_a.forward(self.real_A_encoded, self.real_B_encoded)
      std_a = self.logvar_a.mul(0.5).exp_()
      eps_a = self.get_z_random(std_a.size(0), std_a.size(1), 'gauss')
      self.z_attr_a = eps_a.mul(std_a).add_(self.mu_a)
      std_b = self.logvar_b.mul(0.5).exp_()
      eps_b = self.get_z_random(std_b.size(0), std_b.size(1), 'gauss')
      self.z_attr_b = eps_b.mul(std_b).add_(self.mu_b)
    else:
      self.z_attr_a, self.z_attr_b = self.enc_a.forward(self.real_A_encoded, self.real_B_encoded)

    # get random z_a
    self.z_random = self.get_z_random(self.real_A_encoded.size(0), self.nz, 'gauss')
    self.z_random2 = self.get_z_random(self.real_A_encoded.size(0), self.nz, 'guass')

    # first cross translation
    if self.ms:
      input_content_forA = torch.cat((self.z_content_b, self.z_content_a, self.z_content_b, self.z_content_b),0)
      input_content_forB = torch.cat((self.z_content_a, self.z_content_b, self.z_content_a, self.z_content_a),0)
      input_attr_forA = torch.cat((self.z_attr_a, self.z_attr_a, self.z_random, self.z_random2),0)
      input_attr_forB = torch.cat((self.z_attr_b, self.z_attr_b, self.z_random, self.z_random2),0)
      output_fakeA, output_fakeA_s = self.gen.forward_a(input_content_forA, input_attr_forA)
      output_fakeB, output_fakeB_s = self.gen.forward_b(input_content_forB, input_attr_forB)
      self.fake_A_encoded, self.fake_AA_encoded, self.fake_A_random, self.fake_A_random2 = torch.split(output_fakeA, self.z_content_a.size(0), dim=0)
      self.fake_B_encoded, self.fake_BB_encoded, self.fake_B_random, self.fake_B_random2 = torch.split(output_fakeB, self.z_content_a.size(0), dim=0)
      self.fake_A_encoded_s, self.fake_AA_encoded_s, self.fake_A_random_s, _ = torch.split(output_fakeA_s, self.z_content_a.size(0), dim=0)
      self.fake_B_encoded_s, self.fake_BB_encoded_s, self.fake_B_random_s, _ = torch.split(output_fakeB_s, self.z_content_a.size(0), dim=0)
    else:
      input_content_forA = torch.cat((self.z_content_b, self.z_content_a, self.z_content_b),0)
      input_content_forB = torch.cat((self.z_content_a, self.z_content_b, self.z_content_a),0)
      input_attr_forA = torch.cat((self.z_attr_a, self.z_attr_a, self.z_random),0)
      input_attr_forB = torch.cat((self.z_attr_b, self.z_attr_b, self.z_random),0)
      output_fakeA, output_fakeA_s = self.gen.forward_a(input_content_forA, input_attr_forA)
      output_fakeB, output_fakeB_s = self.gen.forward_b(input_content_forB, input_attr_forB)
      self.fake_A_encoded, self.fake_AA_encoded, self.fake_A_random = torch.split(output_fakeA, self.z_content_a.size(0), dim=0)
      self.fake_B_encoded, self.fake_BB_encoded, self.fake_B_random = torch.split(output_fakeB, self.z_content_a.size(0), dim=0)
      self.fake_A_encoded_s, self.fake_AA_encoded_s, self.fake_A_random_s = torch.split(output_fakeA_s, self.z_content_a.size(0), dim=0)
      self.fake_B_encoded_s, self.fake_BB_encoded_s, self.fake_B_random_s = torch.split(output_fakeB_s, self.z_content_a.size(0), dim=0)
    # get reconstructed encoded z_c
    self.fake_A_encoded = self.fake_A_encoded.cuda(self.gpu2)
    self.fake_B_encoded = self.fake_B_encoded.cuda(self.gpu2)
    z_content_recon_b_1 = self.enc_c_a.forward_features(self.fake_A_encoded)
    z_content_recon_a_1 = self.enc_c_b.forward_features(self.fake_B_encoded)
    self.fake_A_encoded = self.fake_A_encoded.cuda(self.gpu)
    self.fake_B_encoded = self.fake_B_encoded.cuda(self.gpu)

    self.z_content_recon_b,self.z_content_recon_a = self.conv_share(z_content_recon_b_1.cuda(self.gpu),z_content_recon_a_1.cuda(self.gpu)) 
    
    # get reconstructed encoded z_a
    if self.concat:
      self.mu_recon_a, self.logvar_recon_a, self.mu_recon_b, self.logvar_recon_b = self.enc_a.forward(self.fake_A_encoded, self.fake_B_encoded)
      std_a = self.logvar_recon_a.mul(0.5).exp_()
      eps_a = self.get_z_random(std_a.size(0), std_a.size(1), 'gauss')
      self.z_attr_recon_a = eps_a.mul(std_a).add_(self.mu_recon_a)
      std_b = self.logvar_recon_b.mul(0.5).exp_()
      eps_b = self.get_z_random(std_b.size(0), std_b.size(1), 'gauss')
      self.z_attr_recon_b = eps_b.mul(std_b).add_(self.mu_recon_b)
    else:
      self.z_attr_recon_a, self.z_attr_recon_b = self.enc_a.forward(self.fake_A_encoded, self.fake_B_encoded)

    # second cross translation
    self.fake_A_recon, _ = self.gen.forward_a(self.z_content_recon_a, self.z_attr_recon_a)
    self.fake_B_recon, _ = self.gen.forward_b(self.z_content_recon_b, self.z_attr_recon_b)

    # for display
    self.image_display = torch.cat((self.real_A_encoded[0:1].detach().cpu(), self.fake_B_encoded[0:1].detach().cpu(), \
                                    self.fake_B_random[0:1].detach().cpu(), self.fake_AA_encoded[0:1].detach().cpu(), self.fake_A_recon[0:1].detach().cpu(), \
                                    self.real_B_encoded[0:1].detach().cpu(), self.fake_A_encoded[0:1].detach().cpu(), \
                                    self.fake_A_random[0:1].detach().cpu(), self.fake_BB_encoded[0:1].detach().cpu(), self.fake_B_recon[0:1].detach().cpu()), dim=0)

    # for latent regression
    if self.concat:
      self.mu2_a, _, self.mu2_b, _ = self.enc_a.forward(self.fake_A_random, self.fake_B_random)
    else:
      self.z_attr_random_a, self.z_attr_random_b = self.enc_a.forward(self.fake_A_random, self.fake_B_random)

  def forward_content(self):
    half_size = 1
    self.real_A_encoded = self.input_A[0:half_size]
    self.real_B_encoded = self.input_B[0:half_size]
    # get encoded z_c
    self.real_A_encoded = self.real_A_encoded.cuda(self.gpu2)
    self.real_B_encoded = self.real_A_encoded.cuda(self.gpu2)
    z_content_a = self.enc_c_a.forward_features(self.real_A_encoded)
    z_content_b = self.enc_c_b.forward_features(self.real_B_encoded)
    self.real_A_encoded = self.real_A_encoded.cuda(self.gpu)
    self.real_B_encoded = self.real_A_encoded.cuda(self.gpu)
    self.z_content_a,self.z_content_b = self.conv_share(z_content_a.cuda(self.gpu),z_content_b.cuda(self.gpu))
    

  def update_D_content(self, image_a, image_b):
    self.input_A = image_a
    self.input_B = image_b
    self.forward_content()
    self.disContent_opt.zero_grad()
    loss_D_Content = self.backward_contentD(self.z_content_a, self.z_content_b)
    self.disContent_loss = loss_D_Content.item()
    nn.utils.clip_grad_norm_(self.disContent.parameters(), 5)
    self.disContent_opt.step()

  def update_D(self, image_a, image_b):
    self.input_A = image_a
    self.input_B = image_b
    self.forward()

    # update disA
    self.disA_opt.zero_grad()
    loss_D1_A = self.backward_D(self.disA, self.real_A_encoded, self.fake_A_encoded, self.fake_A_encoded_s)
    self.disA_loss = loss_D1_A.item()
    self.disA_opt.step()

    # update disA2
    self.disA2_opt.zero_grad()
    loss_D2_A = self.backward_D(self.disA2, self.real_A_random, self.fake_A_random, self.fake_A_random_s)
    self.disA2_loss = loss_D2_A.item()
    self.disA2_opt.step()

    # update disB
    self.disB_opt.zero_grad()
    loss_D1_B = self.backward_D(self.disB, self.real_B_encoded, self.fake_B_encoded, self.fake_B_encoded_s)
    self.disB_loss = loss_D1_B.item()
    self.disB_opt.step()

    # update disB2
    self.disB2_opt.zero_grad()
    loss_D2_B = self.backward_D(self.disB2, self.real_B_random, self.fake_B_random, self.fake_B_random_s)
    self.disB2_loss = loss_D2_B.item()
    self.disB2_opt.step()

    # update disContent
    self.disContent_opt.zero_grad()
    loss_D_Content = self.backward_contentD(self.z_content_a, self.z_content_b)
    self.disContent_loss = loss_D_Content.item()
    nn.utils.clip_grad_norm_(self.disContent.parameters(), 5)
    self.disContent_opt.step()

  def backward_D(self, netD, real, fake, fake_s):
    loss_D = 0
    pred_fake = netD.forward(fake.detach(), fake_s.detach())
    pred_real = netD.forward(real)
    for it, (out_a, out_b) in enumerate(zip(pred_fake, pred_real)):
      out_fake = torch.sigmoid(out_a)
      out_real = torch.sigmoid(out_b)
      all0 = torch.zeros_like(out_fake).cuda(self.gpu)
      all1 = torch.ones_like(out_real).cuda(self.gpu)
      ad_fake_loss = nn.functional.binary_cross_entropy(out_fake, all0)
      ad_true_loss = nn.functional.binary_cross_entropy(out_real, all1)
      loss_D += ad_true_loss + ad_fake_loss
    loss_D.backward()
    return loss_D

  def backward_contentD(self, imageA, imageB):
    pred_fake = self.disContent.forward(imageA.detach())
    pred_real = self.disContent.forward(imageB.detach())
    for it, (out_a, out_b) in enumerate(zip(pred_fake, pred_real)):
      out_fake = torch.sigmoid(out_a)
      out_real = torch.sigmoid(out_b)
      all1 = torch.ones((out_real.size(0))).cuda(self.gpu)
      all0 = torch.zeros((out_fake.size(0))).cuda(self.gpu)
      ad_true_loss = nn.functional.binary_cross_entropy(out_real, all1)
      ad_fake_loss = nn.functional.binary_cross_entropy(out_fake, all0)
    loss_D = ad_true_loss + ad_fake_loss
    loss_D.backward()
    return loss_D

  def update_EG(self):
    # update G, Ec, Ea
    self.enc_c_a_opt.zero_grad()
    self.enc_c_b_opt.zero_grad()
    self.conv_share_opt.zero_grad()
    self.enc_a_opt.zero_grad()
    self.gen_opt.zero_grad()
    self.backward_EG()
    self.enc_c_a_opt.step()
    self.enc_c_b_opt.step()
    self.conv_share_opt.step()
    self.enc_a_opt.step()
    self.gen_opt.step()

    # update G, Ec
    self.enc_c_a_opt.zero_grad()
    self.enc_c_b_opt.zero_grad()
    self.conv_share_opt.zero_grad()
    self.gen_opt.zero_grad()
    self.backward_G_alone()
    self.enc_c_a_opt.step()
    self.enc_c_b_opt.step()
    self.conv_share_opt.step()
    self.gen_opt.step()

  def backward_EG(self):
    # content Ladv for generator
    loss_G_GAN_Acontent = self.backward_G_GAN_content(self.z_content_a)
    loss_G_GAN_Bcontent = self.backward_G_GAN_content(self.z_content_b)

    # Ladv for generator
    loss_G_GAN_A = self.backward_G_GAN(self.fake_A_encoded, self.fake_A_encoded_s, self.disA)
    loss_G_GAN_B = self.backward_G_GAN(self.fake_B_encoded, self.fake_B_encoded_s, self.disB)

    # KL loss - z_a
    if self.concat:
      kl_element_a = self.mu_a.pow(2).add_(self.logvar_a.exp()).mul_(-1).add_(1).add_(self.logvar_a)
      loss_kl_za_a = torch.sum(kl_element_a).mul_(-0.5) * 0.01
      kl_element_b = self.mu_b.pow(2).add_(self.logvar_b.exp()).mul_(-1).add_(1).add_(self.logvar_b)
      loss_kl_za_b = torch.sum(kl_element_b).mul_(-0.5) * 0.01
    else:
      loss_kl_za_a = self._l2_regularize(self.z_attr_a) * 0.01
      loss_kl_za_b = self._l2_regularize(self.z_attr_b) * 0.01

    # KL loss - z_c
    loss_kl_zc_a = self._l2_regularize(self.z_content_a) * 0.01
    loss_kl_zc_b = self._l2_regularize(self.z_content_b) * 0.01

    # cross cycle consistency loss
    loss_G_L1_A = self.criterionL1(self.fake_A_recon, self.real_A_encoded) * 10
    loss_G_L1_B = self.criterionL1(self.fake_B_recon, self.real_B_encoded) * 10
    loss_G_L1_AA = self.criterionL1(self.fake_AA_encoded, self.real_A_encoded) * 10
    loss_G_L1_BB = self.criterionL1(self.fake_BB_encoded, self.real_B_encoded) * 10

    loss_G = loss_G_GAN_A + loss_G_GAN_B + \
             loss_G_GAN_Acontent + loss_G_GAN_Bcontent + \
             loss_G_L1_AA + loss_G_L1_BB + \
             loss_G_L1_A + loss_G_L1_B + \
             loss_kl_zc_a + loss_kl_zc_b + \
             loss_kl_za_a + loss_kl_za_b

    loss_G.backward(retain_graph=True)

    self.gan_loss_a = loss_G_GAN_A.item()
    self.gan_loss_b = loss_G_GAN_B.item()
    self.gan_loss_acontent = loss_G_GAN_Acontent.item()
    self.gan_loss_bcontent = loss_G_GAN_Bcontent.item()
    self.kl_loss_za_a = loss_kl_za_a.item()
    self.kl_loss_za_b = loss_kl_za_b.item()
    self.kl_loss_zc_a = loss_kl_zc_a.item()
    self.kl_loss_zc_b = loss_kl_zc_b.item()
    self.l1_recon_A_loss = loss_G_L1_A.item()
    self.l1_recon_B_loss = loss_G_L1_B.item()
    self.l1_recon_AA_loss = loss_G_L1_AA.item()
    self.l1_recon_BB_loss = loss_G_L1_BB.item()
    self.G_loss = loss_G.item()

  def backward_G_GAN_content(self, data):
    outs = self.disContent.forward(data)
    for out in outs:
      outputs_fake = torch.sigmoid(out)
      all_half = 0.5*torch.ones((outputs_fake.size(0))).cuda(self.gpu)
      ad_loss = nn.functional.binary_cross_entropy(outputs_fake, all_half)
    return ad_loss

  def backward_G_GAN(self, fake, fake_s, netD=None):
    outs_fake = netD.forward(fake, fake_s)
    loss_G = 0
    for out_a in outs_fake:
      outputs_fake = torch.sigmoid(out_a)
      all_ones = torch.ones_like(outputs_fake).cuda(self.gpu)
      loss_G += nn.functional.binary_cross_entropy(outputs_fake, all_ones)
    return loss_G

  def backward_G_alone(self):
    # Ladv for generator
    loss_G_GAN2_A = self.backward_G_GAN(self.fake_A_random, self.fake_A_random_s, self.disA2)
    loss_G_GAN2_B = self.backward_G_GAN(self.fake_B_random, self.fake_B_random_s, self.disB2)

    # mode seeking loss
    if self.ms:
      lz_AB = torch.mean(torch.abs(self.fake_B_random2 - self.fake_B_random)) / torch.mean(torch.abs(self.z_random2 - self.z_random))
      lz_BA = torch.mean(torch.abs(self.fake_A_random2 - self.fake_A_random)) / torch.mean(torch.abs(self.z_random2 - self.z_random))
      loss_lz_AB = 1 / (lz_AB + 1e-5) * 0.1
      loss_lz_BA = 1 / (lz_BA + 1e-5) * 0.1
    else:
      loss_lz_AB = 0.
      loss_lz_BA = 0.

    # latent regression loss
    if self.concat:
      loss_z_L1_a = torch.mean(torch.abs(self.mu2_a - self.z_random)) * 10
      loss_z_L1_b = torch.mean(torch.abs(self.mu2_b - self.z_random)) * 10
    else:
      loss_z_L1_a = torch.mean(torch.abs(self.z_attr_random_a - self.z_random)) * 10
      loss_z_L1_b = torch.mean(torch.abs(self.z_attr_random_b - self.z_random)) * 10

    loss_z_L1 = loss_z_L1_a + loss_z_L1_b + loss_G_GAN2_A + loss_G_GAN2_B + loss_lz_AB + loss_lz_BA
    loss_z_L1.backward()
    self.l1_recon_z_loss_a = loss_z_L1_a.item()
    self.l1_recon_z_loss_b = loss_z_L1_b.item()
    self.gan2_loss_a = loss_G_GAN2_A.item()
    self.gan2_loss_b = loss_G_GAN2_B.item()
    self.lz_loss_AB = loss_lz_AB.item() if self.ms else 0.
    self.lz_loss_BA = loss_lz_BA.item() if self.ms else 0.

  def update_lr(self):
    self.disA_sch.step()
    self.disB_sch.step()
    self.disA2_sch.step()
    self.disB2_sch.step()
    self.disContent_sch.step()
    self.enc_c_a_sch.step()
    self.enc_c_b_sch.step()
    self.enc_a_sch.step()
    self.gen_sch.step()

  def _l2_regularize(self, mu):
    mu_2 = torch.pow(mu, 2)
    encoding_loss = torch.mean(mu_2)
    return encoding_loss

  def resume(self, model_dir, train=True):
    checkpoint = torch.load(model_dir)
    # weight
    if train:
      self.disA.load_state_dict(checkpoint['disA'])
      self.disA2.load_state_dict(checkpoint['disA2'])
      self.disB.load_state_dict(checkpoint['disB'])
      self.disB2.load_state_dict(checkpoint['disB2'])
      self.disContent.load_state_dict(checkpoint['disContent'])
    self.enc_c_a.load_state_dict(checkpoint['enc_c_a'])
    self.enc_c_b.load_state_dict(checkpoint['enc_c_b'])
    self.enc_a.load_state_dict(checkpoint['enc_a'])
    self.gen.load_state_dict(checkpoint['gen'])
    # optimizer
    if train:
      self.disA_opt.load_state_dict(checkpoint['disA_opt'])
      self.disA2_opt.load_state_dict(checkpoint['disA2_opt'])
      self.disB_opt.load_state_dict(checkpoint['disB_opt'])
      self.disB2_opt.load_state_dict(checkpoint['disB2_opt'])
      self.disContent_opt.load_state_dict(checkpoint['disContent_opt'])
      self.enc_c_a_opt.load_state_dict(checkpoint['enc_c_a_opt'])
      self.enc_c_b_opt.load_state_dict(checkpoint['enc_c_b_opt'])
      self.conv_share.load_state_dict(checkpoint['conv_share_opt'])
      self.enc_a_opt.load_state_dict(checkpoint['enc_a_opt'])
      self.gen_opt.load_state_dict(checkpoint['gen_opt'])
    return checkpoint['ep'], checkpoint['total_it']

  def save(self, filename, ep, total_it):
    state = {
             'disA': self.disA.state_dict(),
             'disA2': self.disA2.state_dict(),
             'disB': self.disB.state_dict(),
             'disB2': self.disB2.state_dict(),
             'disContent': self.disContent.state_dict(),
             'enc_c_a': self.enc_c_a.state_dict(),
             'enc_c_b': self.enc_c_b.state_dict(),
             'enc_a': self.enc_a.state_dict(),
             'gen': self.gen.state_dict(),
             'disA_opt': self.disA_opt.state_dict(),
             'disA2_opt': self.disA2_opt.state_dict(),
             'disB_opt': self.disB_opt.state_dict(),
             'disB2_opt': self.disB2_opt.state_dict(),
             'disContent_opt': self.disContent_opt.state_dict(),
             'enc_c_a_opt': self.enc_c_a_opt.state_dict(),
             'enc_c_b_opt': self.enc_c_b_opt.state_dict(),
             'conv_share_opt': self.conv_share_opt.state_dict(),
             'enc_a_opt': self.enc_a_opt.state_dict(),
             'gen_opt': self.gen_opt.state_dict(),
             'ep': ep,
             'total_it': total_it
              }
    torch.save(state, filename)
    return

  def assemble_outputs(self):
    images_a = self.normalize_image(self.real_A_encoded).detach()
    images_b = self.normalize_image(self.real_B_encoded).detach()
    images_a1 = self.normalize_image(self.fake_A_encoded).detach()
    images_a2 = self.normalize_image(self.fake_A_random).detach()
    images_a3 = self.normalize_image(self.fake_A_recon).detach()
    images_a4 = self.normalize_image(self.fake_AA_encoded).detach()
    images_b1 = self.normalize_image(self.fake_B_encoded).detach()
    images_b2 = self.normalize_image(self.fake_B_random).detach()
    images_b3 = self.normalize_image(self.fake_B_recon).detach()
    images_b4 = self.normalize_image(self.fake_BB_encoded).detach()
    row1 = torch.cat((images_a[0:1, ::], images_b1[0:1, ::], images_b2[0:1, ::], images_a4[0:1, ::], images_a3[0:1, ::]),3)
    row2 = torch.cat((images_b[0:1, ::], images_a1[0:1, ::], images_a2[0:1, ::], images_b4[0:1, ::], images_b3[0:1, ::]),3)
    return torch.cat((row1,row2),2)

  def normalize_image(self, x):
    return x[:,0:3,:,:]
